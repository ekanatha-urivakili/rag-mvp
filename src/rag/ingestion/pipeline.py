import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from rag.adapters.embedder import get_embedder, get_sparse_embedder
from rag.adapters.llm.base import RouteEntry
from rag.adapters.storage import get_storage
from rag.adapters.vectorstore import PointIn, get_vectorstore, point_id
from rag.core.config import get_settings
from rag.core.errors import DOCUMENT_PROCESSING_FAILURE, LLMUnavailable
from rag.core.logging import log_extra
from rag.db.models import Chunk, Document, Receipt, User
from rag.email.outbox import queue_email
from rag.ingestion.chunker import chunk_pages
from rag.ingestion.locking import lock_index
from rag.ingestion.parsers import Page, parse
from rag.ingestion.sniff import IMAGES, PDF
from rag.receipts.extract import Receipt as ReceiptData
from rag.receipts.extract import extract_receipt, to_markdown, worth_extracting
from rag.receipts.ocr import llm_image

log = logging.getLogger(__name__)


def storage_key(tenant_id: uuid.UUID, document_id: uuid.UUID, version: int) -> str:
    # Never derived from the user's filename (no path traversal into other tenants' prefixes).
    return f"{tenant_id}/{document_id}/v{version}"


class StaleJob(Exception):
    """The document changed or was deleted after the job was queued."""


async def _extract_receipt(
    raw: bytes, mime: str, pages: list[Page], progress: Callable[[dict[str, str]], Awaitable[None]]
) -> ReceiptData | None:
    if mime not in IMAGES and mime != PDF:
        return None
    text = "\n\n".join(p.markdown for p in pages)
    if not worth_extracting(text, from_image=any(p.ocr for p in pages)):
        return None

    async def announce(entry: RouteEntry) -> None:
        await progress({"step": "extracting", "provider": entry.provider, "model": entry.model})

    image = await asyncio.to_thread(llm_image, raw, mime)
    try:
        return await extract_receipt(image, text, announce)
    except LLMUnavailable:
        # OCR text is still indexed; structured fields need a model (local vision model or a cloud key).
        log.warning("receipt_extraction_unavailable")
        return None


async def ingest_document(db: AsyncSession, payload: dict[str, Any]) -> None:
    s = get_settings()
    doc_id, version = uuid.UUID(payload["document_id"]), int(payload["version"])
    await lock_index(db)
    doc = (await db.execute(select(Document).where(Document.id == doc_id).with_for_update())).scalar_one_or_none()
    if doc is None or doc.status == "deleted" or doc.version != version:
        log.info("ingest_skipped_stale", extra=log_extra(document_id=str(doc_id)))
        return
    doc.status, doc.error, doc.progress = "processing", None, {"step": "reading"}
    tenant_id, mime, kind = doc.tenant_id, doc.mime_type, doc.kind
    await db.commit()

    # Slow work (OCR, LLM extraction) runs without the index lock or the document row lock held.
    async def progress(value: dict[str, str]) -> None:
        await db.execute(
            update(Document).where(Document.id == doc_id, Document.version == version).values(progress=value)
        )
        await db.commit()

    raw = await get_storage().get(storage_key(tenant_id, doc_id, version))
    if mime in IMAGES:
        await progress({"step": "ocr"})
    pages = await asyncio.to_thread(parse, raw, mime)
    receipt = await _extract_receipt(raw, mime, pages, progress) if kind == "receipt" else None
    if receipt is not None:
        pages = [Page(number=1, markdown=to_markdown(receipt)), *pages]
    chunks = await asyncio.to_thread(chunk_pages, pages, s.chunk_tokens, s.chunk_overlap)
    if not chunks:
        raise ValueError("No extractable text")
    await progress({"step": "indexing"})

    await lock_index(db)
    doc = (
        await db.execute(
            select(Document).where(Document.id == doc_id).with_for_update().execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if doc is None or doc.status == "deleted" or doc.version != version:
        return

    await db.execute(delete(Chunk).where(Chunk.document_id == doc.id, Chunk.version == version))
    db.add_all(
        Chunk(
            document_id=doc.id,
            version=version,
            chunk_index=c.index,
            text=c.text,
            heading_path=c.heading_path,
            page_start=c.page_start,
            page_end=c.page_end,
            token_count=c.token_count,
        )
        for c in chunks
    )
    await db.flush()

    embedder, sparse, store = get_embedder(), get_sparse_embedder(), get_vectorstore()
    for i in range(0, len(chunks), s.embedding_batch_size):
        batch = chunks[i : i + s.embedding_batch_size]
        texts = [c.embed_text for c in batch]
        dense, sparse_vecs = await asyncio.gather(embedder.embed(texts), sparse.embed_documents(texts))
        await store.upsert(
            doc.tenant_id,
            [
                PointIn(
                    id=point_id(doc.id, version, c.index),
                    dense=d,
                    sparse=sv,
                    payload={
                        "doc_id": str(doc.id),
                        "doc_version": version,
                        "chunk_index": c.index,
                        "text": c.text,
                        "heading_path": c.heading_path,
                        "source": doc.title,
                        "page": c.page_start,
                        "embedding_model": embedder.model,
                    },
                )
                for c, d, sv in zip(batch, dense, sparse_vecs, strict=True)
            ],
        )

    await store.delete_document(doc.tenant_id, doc.id, keep_version=version)
    await db.execute(delete(Chunk).where(Chunk.document_id == doc.id, Chunk.version != version))
    await db.execute(delete(Receipt).where(Receipt.document_id == doc.id))
    if receipt is not None:
        db.add(Receipt(document_id=doc.id, tenant_id=doc.tenant_id, version=version, **asdict(receipt)))
    doc.status, doc.progress = "ready", None
    await db.commit()
    log.info("ingest_done", extra=log_extra(document_id=str(doc.id), chunks=len(chunks)))


async def on_ingest_failed(db: AsyncSession, payload: dict[str, Any], error: str) -> None:
    await lock_index(db)
    doc = (
        await db.execute(select(Document).where(Document.id == uuid.UUID(payload["document_id"])).with_for_update())
    ).scalar_one_or_none()
    if doc is None or doc.version != int(payload["version"]) or doc.status == "deleted":
        return
    doc.status, doc.error, doc.progress = "failed", DOCUMENT_PROCESSING_FAILURE, None
    uploader = await db.get(User, doc.uploaded_by) if doc.uploaded_by else None
    if uploader is not None:
        queue_email(
            db,
            template="ingestion_failed",
            to=uploader.email,
            context={
                "title": doc.title,
                "error": doc.error,
                "link": f"{get_settings().public_ui_url}/{'receipts' if doc.kind == 'receipt' else 'documents'}",
            },
        )
    await db.commit()


async def purge_document(db: AsyncSession, payload: dict[str, Any]) -> None:
    """Async cleanup after a delete: vectors (again, idempotent), chunks, raw files."""
    await lock_index(db)
    doc = (
        await db.execute(select(Document).where(Document.id == uuid.UUID(payload["document_id"])).with_for_update())
    ).scalar_one_or_none()
    if doc is None or doc.status != "deleted":
        return
    await get_vectorstore().delete_document(doc.tenant_id, doc.id)
    await db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
    await db.execute(delete(Receipt).where(Receipt.document_id == doc.id))
    await get_storage().delete_prefix(f"{doc.tenant_id}/{doc.id}/")
    await db.commit()
