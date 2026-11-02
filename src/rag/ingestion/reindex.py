import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag.adapters.embedder import get_embedder, get_sparse_embedder
from rag.adapters.vectorstore import PointIn, get_vectorstore, point_id
from rag.core.config import get_settings
from rag.core.logging import log_extra
from rag.db.models import Chunk, Document
from rag.ingestion.chunker import ChunkOut
from rag.ingestion.locking import lock_index

log = logging.getLogger(__name__)


async def _embed_and_upsert(
    batch: list[tuple[Chunk, Document]],
    target: str,
    embedder: Any,
    sparse: Any,
    store: Any,
) -> None:
    texts = [
        ChunkOut(c.chunk_index, c.text, c.heading_path, c.page_start, c.page_end, c.token_count).embed_text
        for c, _ in batch
    ]
    dense = await embedder.embed(texts)
    sparse_vecs = await sparse.embed_documents(texts)
    by_tenant: dict[uuid.UUID, list[PointIn]] = {}
    for (c, d), dv, sv in zip(batch, dense, sparse_vecs, strict=True):
        by_tenant.setdefault(d.tenant_id, []).append(
            PointIn(
                id=point_id(d.id, d.version, c.chunk_index),
                dense=dv,
                sparse=sv,
                payload={
                    "doc_id": str(d.id),
                    "doc_version": d.version,
                    "chunk_index": c.chunk_index,
                    "text": c.text,
                    "heading_path": c.heading_path,
                    "source": d.title,
                    "page": c.page_start,
                    "category": d.kind,
                    "doc_type": d.mime_type,
                    "embedding_model": embedder.model,
                },
            )
        )
    for tenant_id, points in by_tenant.items():
        await store.upsert(tenant_id, points, collection=target)


async def reindex(db: AsyncSession, payload: dict[str, Any]) -> None:
    """Rebuilds the index into chunks_v{n} from Postgres (no re-parsing), then flips the alias."""
    s = get_settings()
    store = get_vectorstore()
    target = store.collection_name(int(payload["target_version"]))
    previous = await store.current_collection()
    if previous == target:
        return
    embedder, sparse = get_embedder(), get_sparse_embedder()
    if previous is not None and await store.dense_size(previous) != s.embedding_dim:
        # Dimension change: ingestion would write new-size vectors into the live collection and fail, so it waits
        # for the whole rebuild (phase 2 below then finds nothing to catch up).
        await lock_index(db, exclusive=True)
    await store.drop_collection_if_exists(target)
    await store.create_collection(target)

    # Phase 1: bulk re-embed. Without a dimension change, ingestion, upload and delete keep running.
    indexed_versions: dict[uuid.UUID, tuple[uuid.UUID, int]] = {}
    rows = await db.stream(
        select(Chunk, Document)
        .join(Document, Document.id == Chunk.document_id)
        .where(Document.status == "ready", Chunk.version == Document.version)
        .order_by(Document.id, Chunk.chunk_index)
        .execution_options(yield_per=s.embedding_batch_size)
    )
    count = 0
    async for raw_batch in rows.partitions(s.embedding_batch_size):
        batch = [(c, d) for c, d in raw_batch]
        count += len(batch)
        for _, d in batch:
            indexed_versions[d.id] = (d.tenant_id, d.version)
        await _embed_and_upsert(batch, target, embedder, sparse, store)
    await rows.close()

    # Phase 2: acquire the exclusive lock only to reconcile any in-flight delta and atomically flip the alias.
    await lock_index(db, exclusive=True)
    if await store.current_collection() == target:
        return
    live_docs = {
        r.id: (r.tenant_id, r.version)
        for r in (
            await db.execute(
                select(Document.id, Document.tenant_id, Document.version).where(Document.status == "ready")
            )
        ).all()
    }
    for doc_id, (tenant_id, old_ver) in indexed_versions.items():
        cur = live_docs.get(doc_id)
        if cur is None:
            await store.delete_document(tenant_id, doc_id, collection=target)
        elif cur[1] != old_ver:
            await store.delete_document(tenant_id, doc_id, keep_version=cur[1], collection=target)

    changed_ids = [doc_id for doc_id, tv in live_docs.items() if indexed_versions.get(doc_id) != tv]
    if changed_ids:
        delta_rows = (
            await db.execute(
                select(Chunk, Document)
                .join(Document, Document.id == Chunk.document_id)
                .where(Document.id.in_(changed_ids), Document.status == "ready", Chunk.version == Document.version)
                .order_by(Document.id, Chunk.chunk_index)
            )
        ).all()
        for i in range(0, len(delta_rows), s.embedding_batch_size):
            delta_batch = [(c, d) for c, d in delta_rows[i : i + s.embedding_batch_size]]
            count += len(delta_batch)
            await _embed_and_upsert(delta_batch, target, embedder, sparse, store)

    await store.point_alias_to(target)
    await db.commit()
    log.info("reindex_done", extra=log_extra(collection=target, chunks=count))
