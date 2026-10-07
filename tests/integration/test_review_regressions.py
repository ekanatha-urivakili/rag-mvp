import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import text

from rag.adapters.vectorstore import Hit
from rag.core.config import get_settings
from rag.db.models import Document, Job
from rag.db.session import get_sessionmaker
from rag.retrieval import retriever
from rag.worker import loop
from rag.worker.queue import enqueue
from tests.integration.conftest import Account


@pytest.mark.parametrize("job_type", ["send_email", "reindex", "purge_document"])
@pytest.mark.parametrize("outcome", ["done", "failed", "queued"])
async def test_worker_terminal_payload_scrubbing(job_type: str, outcome: str) -> None:
    payload = {"token": "single-use-token", "value": "keep"}
    attempts = 1 if outcome == "queued" else get_settings().job_max_attempts
    async with get_sessionmaker()() as db:
        job = enqueue(db, job_type, payload)
        job.status, job.attempts, job.error = "running", attempts, "previous failure"
        await db.commit()
        job_id, run_after = job.id, job.run_after
    started = datetime.now(UTC)
    async with get_sessionmaker()() as db:
        if outcome == "done":
            await loop._finish(db, str(job_id), job_type)
        else:
            await loop._fail(db, str(job_id), job_type, payload, attempts, RuntimeError("temporary failure"))
    async with get_sessionmaker()() as db:
        final = await db.get(Job, job_id)
        assert final and final.status == outcome
        expected_payload = {"redacted": True} if job_type == "send_email" and outcome != "queued" else payload
        assert final.payload == expected_payload
        assert final.error == (None if outcome == "done" else "RuntimeError: temporary failure")
        assert final.attempts == attempts
        if outcome == "queued":
            assert final.run_after > started
        else:
            assert final.run_after == run_after


async def test_retrieval_excludes_stale_partial_deleted_and_other_tenant(
    client: httpx.AsyncClient, tenant: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    documents = []
    async with get_sessionmaker()() as db:
        for i, status in enumerate(["ready", "processing", "failed", "deleted", "ready"]):
            doc = Document(
                id=uuid.uuid4(),
                tenant_id=tenant.tenant_id,
                uploaded_by=tenant.user_id,
                title=f"doc{i}",
                source_uri="test",
                mime_type="text/plain",
                size_bytes=1,
                content_hash=str(i),
                version=2,
                status=status,
            )
            db.add(doc)
            documents.append(doc)
        await db.commit()
    hits = [
        Hit(
            id=str(i),
            score=1.0,
            payload={
                "doc_id": str(doc.id),
                "doc_version": 1 if i == 4 else 2,
                "chunk_index": 0,
                "text": "test",
                "source": doc.title,
            },
        )
        for i, doc in enumerate(documents)
    ]
    hits.append(Hit(id="foreign", score=1.0, payload={"doc_id": str(uuid.uuid4()), "doc_version": 2}))
    monkeypatch.setattr(retriever, "get_embedder", lambda: AsyncMock(embed=AsyncMock(return_value=[[1.0]])))
    monkeypatch.setattr(retriever, "get_sparse_embedder", lambda: AsyncMock(embed_query=AsyncMock(return_value=None)))
    monkeypatch.setattr(retriever, "get_vectorstore", lambda: AsyncMock(hybrid_search=AsyncMock(return_value=hits)))
    monkeypatch.setattr(retriever, "get_reranker", lambda: AsyncMock(score=AsyncMock(return_value=[1.0])))
    result = await retriever.retrieve(tenant.tenant_id, "query")
    assert [c.doc_id for c in result.chunks] == [str(documents[0].id)]


async def test_concurrent_uploads_same_title_have_one_document(client: httpx.AsyncClient, tenant: Account) -> None:
    async def upload(content: bytes) -> httpx.Response:
        return await client.post("/v1/documents", files={"file": ("same.txt", content)}, headers=tenant.headers)

    first, second = await asyncio.gather(upload(b"first content"), upload(b"second content"))
    assert first.status_code == second.status_code == 202
    assert first.json()["document_id"] == second.json()["document_id"]
    documents = (await client.get("/v1/documents", headers=tenant.headers)).json()
    assert documents["total"] == 1 and documents["items"][0]["version"] == 2


async def test_failed_reupload_can_retry(client: httpx.AsyncClient, tenant: Account) -> None:
    files = {"file": ("retry.txt", b"retry content")}
    first = await client.post("/v1/documents", files=files, headers=tenant.headers)
    async with get_sessionmaker()() as db:
        doc = await db.get(Document, uuid.UUID(first.json()["document_id"]))
        assert doc
        doc.status, doc.error = "failed", "temporary provider failure"
        await db.commit()
    retry = await client.post("/v1/documents", files=files, headers=tenant.headers)
    assert retry.status_code == 202 and retry.json()["job_id"] != first.json()["job_id"]
    assert retry.json()["document_id"] == first.json()["document_id"]


async def test_live_worker_cannot_be_reclaimed_after_lease_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def handler(db, job_id, payload):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

    monkeypatch.setitem(loop.HANDLERS, "send_email", handler)
    monkeypatch.setattr(loop, "_CLAIM", text(str(loop._CLAIM).replace("15 minutes", "0 seconds")))
    async with get_sessionmaker()() as db:
        job = enqueue(db, "send_email", {"secret": "single-use-token"})
        await db.commit()
    task = asyncio.create_task(loop.run_once())
    try:
        await asyncio.wait_for(started.wait(), 5)
        assert await asyncio.wait_for(loop.run_once(), 5) is False
    finally:
        release.set()
        await task
    assert calls == 1
    async with get_sessionmaker()() as db:
        final = await db.get(Job, job.id)
        assert final and final.status == "done" and final.payload == {"redacted": True}


async def test_long_rate_limit_keys_do_not_collide() -> None:
    from rag.core.ratelimit import hit

    prefix = "email:" + "a" * 200
    async with get_sessionmaker()() as db:
        await hit(db, prefix + "first", 1, 60)
        await hit(db, prefix + "second", 1, 60)
        assert (await db.execute(text("SELECT count(*) FROM rate_limits"))).scalar_one() == 2


async def test_api_caps_chunked_multipart_before_file_processing(
    tenant: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rag.api.main import create_app
    from rag.core.config import get_settings

    monkeypatch.setattr(get_settings(), "max_request_body_bytes", 1024)

    async def chunks():
        yield b'--test\r\nContent-Disposition: form-data; name="file"; filename="large.txt"\r\n\r\n'
        yield b"a" * 2048
        yield b"\r\n--test--\r\n"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://localhost"
    ) as client:
        response = await client.post(
            "/v1/documents",
            content=chunks(),
            headers={**tenant.headers, "Content-Type": "multipart/form-data; boundary=test"},
        )
    assert response.status_code == 413
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_reindex_retry_removes_ghosts_and_preserves_previous_collection(
    client: httpx.AsyncClient, tenant: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qdrant_client import models as qm

    from rag.adapters.vectorstore import PointIn, get_vectorstore
    from rag.db.models import Chunk
    from rag.ingestion import reindex as module

    store = get_vectorstore()
    previous = await store.current_collection()
    target = store.collection_name(991)
    assert previous != target
    await store.drop_collection_if_exists(target)
    await store.create_collection(target)
    dim = module.get_settings().embedding_dim
    sparse = qm.SparseVector(indices=[1], values=[1.0])
    await store.upsert(
        tenant.tenant_id,
        [
            PointIn(
                id=str(uuid.uuid4()),
                dense=[1.0] * dim,
                sparse=sparse,
                payload={"doc_id": str(uuid.uuid4()), "doc_version": 1},
            )
        ],
        collection=target,
    )
    doc_id = uuid.uuid4()
    async with get_sessionmaker()() as db:
        db.add(
            Document(
                id=doc_id,
                tenant_id=tenant.tenant_id,
                uploaded_by=tenant.user_id,
                title="reindex guide",
                source_uri="test",
                mime_type="text/plain",
                size_bytes=1,
                content_hash="reindex",
                version=1,
                status="ready",
            )
        )
        await db.flush()
        db.add(
            Chunk(
                document_id=doc_id,
                version=1,
                chunk_index=0,
                text="indexed text",
                heading_path="",
                page_start=None,
                page_end=None,
                token_count=2,
            )
        )
        await db.commit()
    monkeypatch.setattr(
        module, "get_embedder", lambda: AsyncMock(model="test", embed=AsyncMock(return_value=[[1.0] * dim]))
    )
    monkeypatch.setattr(
        module, "get_sparse_embedder", lambda: AsyncMock(embed_documents=AsyncMock(return_value=[sparse]))
    )
    try:
        async with get_sessionmaker()() as db:
            await module.reindex(db, {"target_version": 991})
        assert await store.current_collection() == target
        hits = await store.hybrid_search(tenant.tenant_id, [1.0] * dim, sparse, 10)
        assert len(hits) == 1 and hits[0].payload["doc_id"] == str(doc_id)
        assert previous and await store._client.collection_exists(previous)
    finally:
        if previous:
            await store.point_alias_to(previous)
        await store.drop_collection_if_exists(target)
