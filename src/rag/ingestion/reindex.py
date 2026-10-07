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


async def reindex(db: AsyncSession, payload: dict[str, Any]) -> None:
    """Rebuilds the index into chunks_v{n} from Postgres (no re-parsing), then flips the alias."""
    s = get_settings()
    store = get_vectorstore()
    target = store.collection_name(int(payload["target_version"]))
    await lock_index(db, exclusive=True)
    previous = await store.current_collection()
    if previous == target:
        return
    embedder, sparse = get_embedder(), get_sparse_embedder()
    await store.drop_collection_if_exists(target)
    await store.create_collection(target)

    rows = await db.stream(
        select(Chunk, Document)
        .join(Document, Document.id == Chunk.document_id)
        .where(Document.status == "ready", Chunk.version == Document.version)
        .order_by(Document.id, Chunk.chunk_index)
        .execution_options(yield_per=s.embedding_batch_size)
    )
    count = 0
    async for batch in rows.partitions(s.embedding_batch_size):
        count += len(batch)
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
                        "embedding_model": embedder.model,
                    },
                )
            )
        for tenant_id, points in by_tenant.items():
            await store.upsert(tenant_id, points, collection=target)

    await store.point_alias_to(target)
    log.info("reindex_done", extra=log_extra(collection=target, chunks=count))
