import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

from rag.adapters.vectorstore import Hit
from rag.core.config import get_settings
from rag.db.models import Chunk, Document
from rag.db.session import get_sessionmaker
from rag.domain.models import ScoredChunk
from rag.retrieval import retriever
from rag.retrieval.context import neighboring_chunks
from tests.integration.conftest import Account, make_tenant


async def test_expansion_excludes_foreign_deleted_and_stale_chunks(
    client: httpx.AsyncClient,
    tenant: Account,
) -> None:
    other = await make_tenant(client, "other")
    seeds = []
    async with get_sessionmaker()() as db:
        for i, (owner, status) in enumerate([(tenant, "ready"), (other, "ready"), (tenant, "deleted")]):
            doc = Document(
                id=uuid.uuid4(),
                tenant_id=owner.tenant_id,
                uploaded_by=owner.user_id,
                title=f"doc{i}",
                source_uri="test",
                mime_type="text/plain",
                size_bytes=1,
                content_hash=str(i),
                version=2,
                status=status,
            )
            db.add(doc)
            await db.flush()
            for version in (1, 2):
                for index in range(5):
                    db.add(
                        Chunk(
                            document_id=doc.id,
                            version=version,
                            chunk_index=index,
                            text=f"{i}:{version}:{index}",
                            heading_path="Guide",
                            page_start=index,
                            page_end=index,
                            token_count=5,
                        )
                    )
            for index in (1, 2):
                seeds.append(
                    ScoredChunk(
                        point_id="",
                        doc_id=str(doc.id),
                        doc_version=2,
                        chunk_index=index,
                        text=f"{i}:2:{index}",
                        heading_path="Guide",
                        source=doc.title,
                        page=index,
                        score=1.0,
                    )
                )
        await db.commit()
    result = await neighboring_chunks(tenant.tenant_id, seeds, 1)
    assert [c.text for c in result] == ["0:2:0", "0:2:3"]
    assert [c.page for c in result] == [0, 3]
    assert all(c.doc_id == seeds[0].doc_id and c.doc_version == 2 for c in result)


async def test_category_filter_checks_authoritative_document_kind(
    client: httpx.AsyncClient,
    tenant: Account,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hits = []
    async with get_sessionmaker()() as db:
        for kind in ("document", "receipt"):
            doc = Document(
                id=uuid.uuid4(),
                tenant_id=tenant.tenant_id,
                uploaded_by=tenant.user_id,
                title=kind,
                source_uri="test",
                mime_type="text/plain",
                size_bytes=1,
                content_hash=kind,
                version=1,
                status="ready",
                kind=kind,
            )
            db.add(doc)
            hits.append(
                Hit(
                    id=str(doc.id),
                    score=1.0,
                    payload={
                        "doc_id": str(doc.id),
                        "doc_version": 1,
                        "chunk_index": 0,
                        "text": kind,
                        "source": kind,
                    },
                )
            )
        await db.commit()
    monkeypatch.setattr(get_settings(), "retrieval_neighbor_radius", 0)
    monkeypatch.setattr(retriever, "get_embedder", lambda: AsyncMock(embed=AsyncMock(return_value=[[1.0]])))
    monkeypatch.setattr(retriever, "get_sparse_embedder", lambda: AsyncMock(embed_query=AsyncMock(return_value=None)))
    monkeypatch.setattr(retriever, "get_vectorstore", lambda: AsyncMock(hybrid_search=AsyncMock(return_value=hits)))
    monkeypatch.setattr(retriever, "get_reranker", lambda: AsyncMock(score=AsyncMock(return_value=[1.0])))
    result = await retriever.retrieve(tenant.tenant_id, "query", {"category": "receipt"})
    assert [c.text for c in result.chunks] == ["receipt"]


async def test_subqueries_rerank_by_best_query_and_hyde_is_search_only(
    client: httpx.AsyncClient,
    tenant: Account,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hits = []
    async with get_sessionmaker()() as db:
        for title in ("alpha", "beta"):
            doc = Document(
                id=uuid.uuid4(),
                tenant_id=tenant.tenant_id,
                uploaded_by=tenant.user_id,
                title=title,
                source_uri="test",
                mime_type="text/plain",
                size_bytes=1,
                content_hash=title,
                version=1,
                status="ready",
            )
            db.add(doc)
            hits.append(
                Hit(
                    id=str(doc.id),
                    score=1.0,
                    payload={"doc_id": str(doc.id), "doc_version": 1, "chunk_index": 0, "text": title, "source": title},
                )
            )
        await db.commit()
    scores = {"compare": [0.1, 0.2], "about alpha": [3.0, -1.0], "about beta": [-1.0, 2.0]}
    embed = AsyncMock(return_value=[[1.0]] * 4)
    sparse = AsyncMock(side_effect=lambda q: q)
    search = AsyncMock(return_value=hits)
    rerank = AsyncMock(side_effect=lambda q, _texts: scores[q])
    monkeypatch.setattr(get_settings(), "retrieval_neighbor_radius", 0)
    monkeypatch.setattr(retriever, "get_embedder", lambda: AsyncMock(embed=embed))
    monkeypatch.setattr(retriever, "get_sparse_embedder", lambda: AsyncMock(embed_query=sparse))
    monkeypatch.setattr(retriever, "get_vectorstore", lambda: AsyncMock(hybrid_search=search))
    monkeypatch.setattr(retriever, "get_reranker", lambda: AsyncMock(score=rerank))

    result = await retriever.retrieve(
        tenant.tenant_id, "compare", subqueries=["about alpha", "about beta"], hypothetical="made-up answer"
    )

    assert embed.call_args.args[0] == ["compare", "about alpha", "about beta", "made-up answer"]
    assert search.call_args.args[2] == ["compare", "about alpha", "about beta"]
    assert [call.args[0] for call in rerank.call_args_list] == ["compare", "about alpha", "about beta"]
    assert [(c.text, c.score) for c in result.chunks] == [("alpha", 3.0), ("beta", 2.0)]
    assert result.reranked
