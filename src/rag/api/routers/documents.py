import contextlib
import hashlib
import uuid
from dataclasses import dataclass
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
from rag.core.errors import AppError, Conflict, NotFound, PayloadTooLarge, UnsupportedMediaType
from rag.db.models import Chunk, Document, Tenant
from rag.domain.models import RequestContext
from rag.ingestion.pipeline import upload_key
from rag.ingestion.sniff import IMAGES, PDF, sanitize_filename, sniff_mime
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
    return await store_upload(file, ctx, db, response, kind="document")


async def store_upload(
    file: UploadFile, ctx: RequestContext, db: DB, response: Response, *, kind: str
) -> UploadAccepted:
    s = get_settings()
    await ratelimit.hit(db, f"upload:tenant:{ctx.tenant_id}", s.rl_upload_per_tenant, s.rl_upload_window_s)
    data = await _read_bounded(file, s.max_upload_bytes)
    if not data:
        raise AppError("Empty file", code="empty_file")
    title = sanitize_filename(file.filename)
    mime = sniff_mime(data, title)
    if kind == "receipt" and mime not in {*IMAGES, PDF}:
        raise UnsupportedMediaType("Receipts must be PDF, JPEG, PNG or WebP")
    upload = _Upload(title=title, kind=kind, mime=mime, size=len(data), content_hash=hashlib.sha256(data).hexdigest())
    storage = get_storage()

    for _ in range(3):
        # Unlocked reads pick the target document; the S3 put then runs with no transaction or lock held.
        live = Document.status != "deleted"
        mine = (Document.tenant_id == ctx.tenant_id, Document.kind == kind, live)
        duplicate = (
            await db.execute(select(Document.id).where(*mine, Document.content_hash == upload.content_hash))
        ).scalar_one_or_none()
        existing_id = (await db.execute(select(Document.id).where(*mine, Document.title == title))).scalar_one_or_none()
        await db.commit()

        doc_id = existing_id or uuid.uuid4()
        key: str | None = None
        if duplicate is None:
            key = upload_key(ctx.tenant_id, doc_id)
            await storage.put(key, data, mime)
        try:
            accepted = await _register_upload(db, ctx, response, upload, key, doc_id, existing=existing_id is not None)
        except BaseException:
            await db.rollback()
            if key is not None:
                with contextlib.suppress(Exception):
                    await storage.delete(key)
            raise
        if key is not None and (accepted is None or not accepted.used_key):
            with contextlib.suppress(Exception):
                await storage.delete(key)
        if accepted is not None:
            return accepted.result
    raise Conflict("The document changed while uploading. Try again.", code="upload_conflict")


@dataclass(frozen=True)
class _Upload:
    title: str
    kind: str
    mime: str
    size: int
    content_hash: str


@dataclass(frozen=True)
class _Registered:
    result: UploadAccepted
    used_key: bool


async def _register_upload(
    db: DB,
    ctx: RequestContext,
    response: Response,
    upload: _Upload,
    key: str | None,
    doc_id: uuid.UUID,
    *,
    existing: bool,
) -> _Registered | None:
    """Records the upload in one short transaction. None means a concurrent change invalidated `key`; retry."""
    s = get_settings()
    live = Document.status != "deleted"
    mine = (Document.tenant_id == ctx.tenant_id, Document.kind == upload.kind, live)
    await db.execute(select(Tenant.id).where(Tenant.id == ctx.tenant_id).with_for_update())
    same = (
        await db.execute(select(Document).where(*mine, Document.content_hash == upload.content_hash))
    ).scalar_one_or_none()
    if same is not None:
        if same.status == "failed":
            same.status, same.error = "queued", None
            job = enqueue(db, "ingest_document", {"document_id": str(same.id), "version": same.version})
            await db.commit()
            return _Registered(UploadAccepted(document_id=same.id, job_id=job.id, status=same.status), False)
        accepted = UploadAccepted(document_id=same.id, job_id=None, status=same.status)
        await db.rollback()  # releases the tenant lock; expires `same`
        response.status_code = status.HTTP_200_OK  # idempotent re-upload: no-op
        return _Registered(accepted, False)
    if key is None:
        await db.rollback()
        return None

    doc = (
        await db.execute(select(Document).where(*mine, Document.title == upload.title).with_for_update())
    ).scalar_one_or_none()
    if (doc.id if doc else None) != (doc_id if existing else None):
        await db.rollback()
        return None
    if doc is None:
        doc = Document(
            id=doc_id,
            tenant_id=ctx.tenant_id,
            uploaded_by=ctx.user_id,
            title=upload.title,
            kind=upload.kind,
            version=1,
            mime_type=upload.mime,
            size_bytes=upload.size,
            content_hash=upload.content_hash,
            status="queued",
            source_uri="",
        )
        db.add(doc)
    else:  # changed content under the same name → new version
        doc.version += 1
        doc.mime_type, doc.size_bytes, doc.content_hash = upload.mime, upload.size, upload.content_hash
        doc.status, doc.error, doc.uploaded_by = "queued", None, ctx.user_id
    doc.source_uri = f"s3://{s.s3_bucket}/{key}"
    version = doc.version
    job = enqueue(db, "ingest_document", {"document_id": str(doc_id), "version": version})
    try:
        await db.commit()
    except IntegrityError:
        # Concurrent upload of identical content won the race.
        await db.rollback()
        winner = (
            await db.execute(select(Document).where(*mine, Document.content_hash == upload.content_hash))
        ).scalar_one()
        response.status_code = status.HTTP_200_OK
        return _Registered(UploadAccepted(document_id=winner.id, job_id=None, status=winner.status), False)
    return _Registered(UploadAccepted(document_id=doc_id, job_id=job.id, status="queued"), True)


@router.get("", response_model=Page[DocumentOut])
async def list_documents(
    ctx: Reader,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    kind: Annotated[str, Query(pattern="^(document|receipt)$")] = "document",
) -> Page[DocumentOut]:
    where = (Document.tenant_id == ctx.tenant_id, Document.status != "deleted", Document.kind == kind)
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
    doc_id = doc.id
    doc.status = "deleted"
    enqueue(db, "purge_document", {"document_id": str(doc_id)})
    audit(
        db,
        action="document.deleted",
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        target_type="document",
        target_id=doc_id,
        ip=ctx.ip,
        title=doc.title,
    )
    await db.commit()
    # Best-effort immediate removal after DB commit; `purge_document` worker job guarantees retry on failure.
    with contextlib.suppress(Exception):
        await get_vectorstore().delete_document(ctx.tenant_id, doc_id)
