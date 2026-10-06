import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.selectable import ScalarSelect

from rag.adapters.storage import get_storage
from rag.adapters.vectorstore import get_vectorstore
from rag.api.schemas import DocumentOut, Page, UploadAccepted
from rag.auth.deps import DB, require
from rag.auth.rbac import Permission
from rag.core import ratelimit
from rag.core.audit import audit
from rag.core.config import get_settings
from rag.core.errors import AppError, NotFound, PayloadTooLarge
from rag.db.models import Chunk, Document
from rag.domain.models import RequestContext
from rag.ingestion.pipeline import storage_key
from rag.ingestion.sniff import sanitize_filename, sniff_mime
from rag.worker.queue import enqueue

router = APIRouter(prefix="/v1/documents", tags=["documents"])

Reader = Annotated[RequestContext, Depends(require(Permission.DOCUMENT_READ))]
Writer = Annotated[RequestContext, Depends(require(Permission.DOCUMENT_WRITE))]
Deleter = Annotated[RequestContext, Depends(require(Permission.DOCUMENT_DELETE))]


async def _read_bounded(file: UploadFile, limit: int) -> bytes:
    buf = bytearray()
    while chunk := await file.read(1024 * 1024):
        buf.extend(chunk)
        if len(buf) > limit:
            raise PayloadTooLarge(f"File exceeds {limit // (1024 * 1024)} MB")
    return bytes(buf)


def _chunk_count_subq() -> ScalarSelect[int]:
    return (
        select(func.count())
        .select_from(Chunk)
        .where(Chunk.document_id == Document.id, Chunk.version == Document.version)
        .scalar_subquery()
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=UploadAccepted)
async def upload(file: UploadFile, ctx: Writer, db: DB, response: Response) -> UploadAccepted:
    s = get_settings()
    await ratelimit.hit(db, f"upload:tenant:{ctx.tenant_id}", s.rl_upload_per_tenant, s.rl_upload_window_s)
    data = await _read_bounded(file, s.max_upload_bytes)
    if not data:
        raise AppError("Empty file", code="empty_file")
    title = sanitize_filename(file.filename)
    mime = sniff_mime(data, title)
    content_hash = hashlib.sha256(data).hexdigest()

    live = Document.status != "deleted"
    same = (
        await db.execute(
            select(Document).where(Document.tenant_id == ctx.tenant_id, Document.content_hash == content_hash, live)
        )
    ).scalar_one_or_none()
    if same is not None:
        response.status_code = status.HTTP_200_OK  # idempotent re-upload: no-op
        return UploadAccepted(document_id=same.id, job_id=None, status=same.status)

    doc = (
        await db.execute(
            select(Document).where(Document.tenant_id == ctx.tenant_id, Document.title == title, live).with_for_update()
        )
    ).scalar_one_or_none()
    if doc is None:
        doc = Document(
            id=uuid.uuid4(),
            tenant_id=ctx.tenant_id,
            uploaded_by=ctx.user_id,
            title=title,
            version=1,
            mime_type=mime,
            size_bytes=len(data),
            content_hash=content_hash,
            status="queued",
            source_uri="",
        )
        db.add(doc)
    else:  # changed content under the same name → new version
        doc.version += 1
        doc.mime_type, doc.size_bytes, doc.content_hash = mime, len(data), content_hash
        doc.status, doc.error, doc.uploaded_by = "queued", None, ctx.user_id

    key = storage_key(ctx.tenant_id, doc.id, doc.version)
    doc.source_uri = f"s3://{s.s3_bucket}/{key}"
    await get_storage().put(key, data, mime)
    job = enqueue(db, "ingest_document", {"document_id": str(doc.id), "version": doc.version})
    try:
        await db.commit()
    except IntegrityError:
        # Concurrent upload of identical content won the race.
        await db.rollback()
        existing = (
            await db.execute(
                select(Document).where(Document.tenant_id == ctx.tenant_id, Document.content_hash == content_hash, live)
            )
        ).scalar_one()
        response.status_code = status.HTTP_200_OK
        return UploadAccepted(document_id=existing.id, job_id=None, status=existing.status)
    return UploadAccepted(document_id=doc.id, job_id=job.id, status=doc.status)


@router.get("", response_model=Page[DocumentOut])
async def list_documents(
    ctx: Reader,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> Page[DocumentOut]:
    where = (Document.tenant_id == ctx.tenant_id, Document.status != "deleted")
    total = (await db.execute(select(func.count()).select_from(Document).where(*where))).scalar_one()
    rows = (
        await db.execute(
            select(Document, _chunk_count_subq().label("n"))
            .where(*where)
            .order_by(Document.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return Page[DocumentOut](
        items=[DocumentOut.model_validate(d).model_copy(update={"chunk_count": n}) for d, n in rows], total=total
    )


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(document_id: uuid.UUID, ctx: Reader, db: DB) -> DocumentOut:
    row = (
        await db.execute(
            select(Document, _chunk_count_subq().label("n")).where(
                Document.id == document_id, Document.tenant_id == ctx.tenant_id, Document.status != "deleted"
            )
        )
    ).one_or_none()
    if row is None:
        raise NotFound("Document not found")  # same response for other tenants' IDs (no BOLA oracle)
    doc, n = row
    return DocumentOut.model_validate(doc).model_copy(update={"chunk_count": n})


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: uuid.UUID, ctx: Deleter, db: DB) -> None:
    doc = (
        await db.execute(
            select(Document)
            .where(Document.id == document_id, Document.tenant_id == ctx.tenant_id, Document.status != "deleted")
            .with_for_update()
        )
    ).scalar_one_or_none()
    if doc is None:
        raise NotFound("Document not found")
    await get_vectorstore().delete_document(ctx.tenant_id, doc.id)
    doc.status = "deleted"
    enqueue(db, "purge_document", {"document_id": str(doc.id)})
    audit(
        db,
        action="document.deleted",
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        target_type="document",
        target_id=doc.id,
        ip=ctx.ip,
        title=doc.title,
    )
    await db.commit()
