import logging
import uuid
from dataclasses import dataclass

from rag.adapters.embedder import get_embedder, get_sparse_embedder
from rag.adapters.reranker import get_reranker
from rag.adapters.vectorstore import get_vectorstore
from rag.core.config import get_settings
from rag.core.logging import log_extra
from rag.domain.models import ScoredChunk

log = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    chunks: list[ScoredChunk]
    reranked: bool


async def retrieve(tenant_id: uuid.UUID, query: str, filters: dict[str, str] | None = None) -> RetrievalResult:
    """Hybrid (dense + BM25, RRF) → cross-encoder rerank → top-k. Degrades gracefully on partial failure."""
    s = get_settings()
    dense = (await get_embedder().embed([query]))[0]
    try:
        sparse = await get_sparse_embedder().embed_query(query)
    except Exception:
        log.warning("sparse_embed_failed_dense_only", exc_info=True)
        sparse = None

    hits = await get_vectorstore().hybrid_search(tenant_id, dense, sparse, s.retrieval_prefetch, filters)
    chunks = [
        ScoredChunk(
            point_id=h.id,
            doc_id=h.payload["doc_id"],
            doc_version=h.payload["doc_version"],
            chunk_index=h.payload["chunk_index"],
            text=h.payload["text"],
            heading_path=h.payload.get("heading_path", ""),
            source=h.payload.get("source", ""),
            page=h.payload.get("page"),
            score=h.score,
        )
        for h in hits
    ]
    if not chunks:
        return RetrievalResult([], reranked=False)

    reranked = False
    if s.reranker_enabled:
        try:
            scores = await get_reranker().score(
                query, [f"{c.heading_path}\n{c.text}" if c.heading_path else c.text for c in chunks]
            )
            for c, sc in zip(chunks, scores, strict=True):
                c.score = sc
            chunks.sort(key=lambda c: c.score, reverse=True)
            reranked = True
        except Exception:
            log.warning("rerank_failed_using_rrf", exc_info=True)
    top = chunks[: s.retrieval_top_k]
    log.info(
        "retrieved",
        extra=log_extra(candidates=len(hits), top_score=round(top[0].score, 4), reranked=reranked),
    )
    return RetrievalResult(top, reranked)
