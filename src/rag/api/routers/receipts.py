import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, UploadFile
from sqlalchemy import Select, func, select

from rag.api.routers.documents import Writer, store_upload
from rag.api.schemas import Page, ReceiptDetailOut, ReceiptOut, UploadAccepted
from rag.auth.deps import DB, require
from rag.auth.rbac import Permission
from rag.core.errors import NotFound
from rag.db.models import Document, Receipt
from rag.domain.models import RequestContext

router = APIRouter(prefix="/v1/receipts", tags=["receipts"])

Reader = Annotated[RequestContext, Depends(require(Permission.DOCUMENT_READ))]


def _visible(ctx: RequestContext) -> Select[Receipt, str]:
    # Tenant from the token; only receipts of live documents at their current version.
    return (
        select(Receipt, Document.title)
        .join(Document, Document.id == Receipt.document_id)
        .where(
            Receipt.tenant_id == ctx.tenant_id,
            Document.tenant_id == ctx.tenant_id,
            Document.status == "ready",
            Document.kind == "receipt",
            Document.version == Receipt.version,
        )
    )


def _fields(receipt: Receipt, title: str) -> dict[str, Any]:
    return {**{c.key: getattr(receipt, c.key) for c in Receipt.__table__.columns}, "title": title}


@router.get("", response_model=Page[ReceiptOut])
async def list_receipts(
    ctx: Reader,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> Page[ReceiptOut]:
    q = _visible(ctx)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (
        await db.execute(
            q.order_by(Receipt.purchased_on.desc().nulls_last(), Receipt.created_at.desc()).limit(limit).offset(offset)
        )
    ).all()
    return Page[ReceiptOut](items=[ReceiptOut.model_validate(_fields(r, t)) for r, t in rows], total=total)


@router.get("/{document_id}", response_model=ReceiptDetailOut)
async def get_receipt(document_id: uuid.UUID, ctx: Reader, db: DB) -> ReceiptDetailOut:
    row = (await db.execute(_visible(ctx).where(Receipt.document_id == document_id))).one_or_none()
    if row is None:
        raise NotFound("Receipt not found")  # same response for other tenants' IDs
    receipt, title = row
    return ReceiptDetailOut.model_validate(_fields(receipt, title))


@router.post("", status_code=202, response_model=UploadAccepted)
async def upload_receipt(file: UploadFile, ctx: Writer, db: DB, response: Response) -> UploadAccepted:
    return await store_upload(file, ctx, db, response, kind="receipt")
