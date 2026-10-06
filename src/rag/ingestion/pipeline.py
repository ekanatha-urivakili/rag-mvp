import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from rag.adapters.embedder import get_embedder, get_sparse_embedder
from rag.adapters.storage import get_storage
from rag.adapters.vectorstore import PointIn, get_vectorstore, point_id
from rag.core.config import get_settings
from rag.core.logging import log_extra
from rag.db.models import Chunk, Document, User
from rag.email.outbox import queue_email
from rag.ingestion.chunker import chunk_pages
from rag.ingestion.parsers import parse

log = logging.getLogger(__name__)


def storage_key(tenant_id: uuid.UUID, document_id: uuid.UUID, version: int) -> str:
    # Never derived from the user's filename (no path traversal into other tenants' prefixes).
    return f"{tenant_id}/{document_id}/v{version}"


class StaleJob(Exception):
    """The document changed or was deleted after the job was queued."""


async def ingest_document(db: AsyncSession, payload: dict[str, Any]) -> None:
    s = get_settings()
    doc_id, version = uuid.UUID(payload["document_id"]), int(payload["version"])
    doc = await db.get(Document, doc_id)
    if doc is None or doc.status == "deleted" or doc.version != version:
        log.info("ingest_skipped_stale", extra=log_extra(document_id=str(doc_id)))
        return
    doc.status, doc.error = "processing", None
    await db.commit()

    raw = await get_storage().get(storage_key(doc.tenant_id, doc.id, version))
    pages = await asyncio.to_thread(parse, raw, doc.mime_type)
    chunks = await asyncio.to_thread(chunk_pages, pages, s.chunk_tokens, s.chunk_overlap)
    if not chunks:
        raise ValueError("No extractable text (scanned PDFs are not supported yet)")

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

    # Re-check: if the doc was deleted or replaced mid-run, don't resurrect it.
    await db.refresh(doc)
    if doc.status == "deleted" or doc.version != version:
        await db.rollback()
        await store.delete_document(doc.tenant_id, doc.id, keep_version=doc.version)
        return
    await store.delete_document(doc.tenant_id, doc.id, keep_version=version)
    await db.execute(delete(Chunk).where(Chunk.document_id == doc.id, Chunk.version != version))
    doc.status = "ready"
    await db.commit()
    log.info("ingest_done", extra=log_extra(document_id=str(doc.id), chunks=len(chunks)))


async def on_ingest_failed(db: AsyncSession, payload: dict[str, Any], error: str) -> None:
    doc = await db.get(Document, uuid.UUID(payload["document_id"]))
    if doc is None or doc.version != int(payload["version"]) or doc.status == "deleted":
        return
    doc.status, doc.error = "failed", error[:500]
    uploader = await db.get(User, doc.uploaded_by) if doc.uploaded_by else None
    if uploader is not None:
        queue_email(
            db,
            template="ingestion_failed",
            to=uploader.email,
            context={"title": doc.title, "error": doc.error, "link": f"{get_settings().public_ui_url}/documents"},
        )
    await db.commit()


async def purge_document(db: AsyncSession, payload: dict[str, Any]) -> None:
    """Async cleanup after a delete: vectors (again, idempotent), chunks, raw files."""
    doc = (
        await db.execute(select(Document).where(Document.id == uuid.UUID(payload["document_id"])))
    ).scalar_one_or_none()
    if doc is None or doc.status != "deleted":
        return
    await get_vectorstore().delete_document(doc.tenant_id, doc.id)
    await db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
    await get_storage().delete_prefix(f"{doc.tenant_id}/{doc.id}/")
    await db.commit()
