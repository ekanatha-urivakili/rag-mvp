import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from opentelemetry import trace
from sqlalchemy import select

from rag.adapters.embedder import get_embedder, get_sparse_embedder
from rag.adapters.reranker import get_reranker
from rag.adapters.vectorstore import get_vectorstore
from rag.core.config import get_settings
from rag.core.logging import log_extra
from rag.core.metrics import RETRIEVAL_DEGRADED, TOP_SCORE
from rag.db.models import Document
from rag.db.session import get_sessionmaker
from rag.domain.models import ScoredChunk
from rag.retrieval.context import neighboring_chunks, select_context

log = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    chunks: list[ScoredChunk]
    reranked: bool


async def retrieve(
    tenant_id: uuid.UUID,
    query: str,
    filters: dict[str, str] | None = None,
    *,
    subqueries: Sequence[str] = (),
    hypothetical: str | None = None,
) -> RetrievalResult:
    """Hybrid (dense + BM25, RRF) → cross-encoder rerank → top-k. Degrades gracefully on partial failure.

    Subqueries add dense and sparse searches to the same fusion, and each chunk keeps its best rerank score
    across the query and subqueries. A hypothetical answer (HyDE) adds a dense search only: it is never a
    rerank query and never returned as evidence.
    """
    s = get_settings()
    queries = [query, *subqueries]
    dense = await get_embedder().embed([*queries, *([hypothetical] if hypothetical else [])])
    sparse_embedder = get_sparse_embedder()
    try:
        sparse = [await sparse_embedder.embed_query(q) for q in queries]
    except Exception:
        log.warning("sparse_embed_failed_dense_only", exc_info=True)
        RETRIEVAL_DEGRADED.labels("sparse").inc()
        sparse = []

    hits = await get_vectorstore().hybrid_search(tenant_id, dense, sparse, s.retrieval_prefetch, filters)
    # Qdrant is derived state: failed, deleted and superseded versions cannot be answer context.
    async with get_sessionmaker()() as db:
        versions = dict(
            (
                await db.execute(
                    select(Document.id, Document.version).where(
                        Document.tenant_id == tenant_id,
                        Document.status == "ready",
                        *([Document.kind == filters["category"]] if filters and "category" in filters else []),
                        Document.id.in_([uuid.UUID(h.payload["doc_id"]) for h in hits]),
                    )
                )
            ).all()
        )
    hits = [h for h in hits if versions.get(uuid.UUID(h.payload["doc_id"])) == h.payload["doc_version"]]
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
            texts = [f"{c.heading_path}\n{c.text}" if c.heading_path else c.text for c in chunks]
            per_query = [await get_reranker().score(q, texts) for q in queries]
            for c, sc in zip(chunks, map(max, zip(*per_query, strict=True)), strict=True):
                c.score = sc
            chunks.sort(key=lambda c: c.score, reverse=True)
            reranked = True
        except Exception:
            log.warning("rerank_failed_using_rrf", exc_info=True)
            RETRIEVAL_DEGRADED.labels("rerank").inc()
    top = select_context(chunks[: s.retrieval_top_k], s.context_max_tokens)
    neighbors = await neighboring_chunks(tenant_id, top, s.retrieval_neighbor_radius)
    top = select_context([*top, *neighbors], s.context_max_tokens)
    if not top:
        return RetrievalResult([], reranked=reranked)
    if reranked:
        TOP_SCORE.observe(top[0].score)
    span = trace.get_current_span()
    span.set_attribute("rag.retrieval.candidates", len(hits))
    span.set_attribute("rag.retrieval.top_score", top[0].score)
    span.set_attribute("rag.retrieval.reranked", reranked)
    span.set_attribute("rag.retrieval.queries", len(queries))
    span.set_attribute("rag.retrieval.hyde", hypothetical is not None)
    log.info(
        "retrieved",
        extra=log_extra(candidates=len(hits), top_score=round(top[0].score, 4), reranked=reranked),
    )
    return RetrievalResult(top, reranked)
