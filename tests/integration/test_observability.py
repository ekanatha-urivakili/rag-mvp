"""/metrics access control and DB-derived gauges; sampled online answer judging end to end (judge faked)."""

import uuid
from collections.abc import AsyncIterator
from typing import Any, ClassVar

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from rag.adapters.llm import router as router_mod
from rag.api.routers import chat as chat_router
from rag.core.config import get_settings
from rag.db.models import AnswerEval, Chunk, Document, Job
from rag.db.session import get_sessionmaker
from rag.domain.models import Citation, ScoredChunk
from rag.evaluation import judge as judge_mod
from rag.evaluation.judge import Judgement, judge_answer
from rag.worker.queue import enqueue
from tests.integration.conftest import Account


async def test_metrics_open_outside_prod_and_reports_job_backlog(client: httpx.AsyncClient) -> None:
    async with get_sessionmaker()() as db:
        enqueue(db, "reindex", {})
        await db.commit()
    r = await client.get("/metrics")
    assert r.status_code == 200
    assert 'jobs_queued{type="reindex"} 1.0' in r.text
    assert "rag_online_judged_24h 0.0" in r.text
    assert 'http_requests_total{method="GET",route="/metrics"' in (await client.get("/metrics")).text


async def test_metrics_token_required_when_configured(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "metrics_token", SecretStr("scrape-secret"))
    assert (await client.get("/metrics")).status_code == 401
    assert (await client.get("/metrics", headers={"Authorization": "Bearer wrong"})).status_code == 401
    assert (await client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"})).status_code == 200


async def test_metrics_disabled_in_prod_without_token(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "env", "prod")
    assert (await client.get("/metrics")).status_code == 404


async def _document_with_chunks(owner: Account, texts: list[str]) -> Document:
    async with get_sessionmaker()() as db:
        doc = Document(
            id=uuid.uuid4(),
            tenant_id=owner.tenant_id,
            title="handbook.pdf",
            source_uri="s3://x",
            mime_type="application/pdf",
            size_bytes=1,
            content_hash=uuid.uuid4().hex,
            version=1,
            status="ready",
        )
        db.add(doc)
        await db.flush()
        for i, t in enumerate(texts):
            db.add(Chunk(document_id=doc.id, version=1, chunk_index=i, text=t, heading_path="", token_count=3))
        await db.commit()
        return doc


class _CitedGraph:
    doc: ClassVar[Document]

    async def astream(self, state: dict[str, Any], **_: Any) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        d = _CitedGraph.doc
        chunks = [
            ScoredChunk(
                point_id=str(i),
                doc_id=str(d.id),
                doc_version=1,
                chunk_index=i,
                text="t",
                heading_path="",
                source=d.title,
                page=None,
                score=1.0,
            )
            for i in range(2)
        ]
        citation = Citation(n=1, doc_id=str(d.id), source=d.title, page=None, snippet="t")
        path = ["analyze_query", "retrieve", "generate", "validate_citations"]
        yield (
            "values",
            {**state, "answer": "Leave is 20 days [1].", "citations": [citation], "chunks": chunks, "path": path},
        )


class _JudgeAvailable:
    def has_provider(self, purpose: str) -> bool:
        return purpose == "eval_judge"


async def test_sampled_answer_is_judged_off_the_hot_path(
    client: httpx.AsyncClient, tenant: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    _CitedGraph.doc = await _document_with_chunks(tenant, ["Annual leave is 20 days.", "Sick leave is 10 days."])
    monkeypatch.setattr(chat_router, "build_graph", _CitedGraph)
    monkeypatch.setattr(router_mod, "get_router", _JudgeAvailable)
    monkeypatch.setattr(get_settings(), "online_eval_sample_rate", 1.0)

    async with client.stream("POST", "/v1/chat", json={"message": "How much leave?"}, headers=tenant.headers) as r:
        assert "event: done" in (await r.aread()).decode()

    async with get_sessionmaker()() as db:
        job = (await db.execute(select(Job).where(Job.type == "judge_answer"))).scalar_one()
    assert job.payload["chunks"] == [[str(_CitedGraph.doc.id), 1, 0], [str(_CitedGraph.doc.id), 1, 1]]

    seen: dict[str, str] = {}

    async def fake_judge(context: str, question: str, answer: str) -> tuple[Judgement, str, str]:
        seen.update(context=context, question=question, answer=answer)
        return Judgement(faithfulness=0.75, relevancy=1.0), "openai", "judge-m"

    monkeypatch.setattr(judge_mod, "judge", fake_judge)
    async with get_sessionmaker()() as db:
        await judge_answer(db, job.payload)
    assert seen["question"] == "How much leave?" and seen["answer"] == "Leave is 20 days [1]."
    assert seen["context"].index("Annual leave") < seen["context"].index("Sick leave")  # citation order kept

    async with get_sessionmaker()() as db:
        ev = (await db.execute(select(AnswerEval))).scalar_one()
    assert (ev.faithfulness, ev.judge_model) == (0.75, "judge-m")
    metrics = (await client.get("/metrics")).text
    assert "rag_online_judged_24h 1.0" in metrics and "rag_online_faithfulness_avg_24h 0.75" in metrics


async def test_unsampled_answers_queue_nothing(
    client: httpx.AsyncClient, tenant: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    _CitedGraph.doc = await _document_with_chunks(tenant, ["a", "b"])
    monkeypatch.setattr(chat_router, "build_graph", _CitedGraph)
    monkeypatch.setattr(router_mod, "get_router", _JudgeAvailable)
    async with client.stream("POST", "/v1/chat", json={"message": "q"}, headers=tenant.headers) as r:
        await r.aread()
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(Job).where(Job.type == "judge_answer"))).first() is None


async def test_judge_skips_when_a_source_was_deleted(tenant: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = await _document_with_chunks(tenant, ["only one"])

    async def must_not_run(*_: str) -> None:
        raise AssertionError("judge called")

    monkeypatch.setattr(judge_mod, "judge", must_not_run)
    async with get_sessionmaker()() as db:
        from rag.db.models import Conversation, Message

        conv = Conversation(id=uuid.uuid4(), tenant_id=tenant.tenant_id, user_id=tenant.user_id, title="t")
        db.add(conv)
        await db.flush()
        db.add(Message(conversation_id=conv.id, role="user", content="q", citations=[]))
        answer = Message(id=uuid.uuid4(), conversation_id=conv.id, role="assistant", content="a [2]", citations=[])
        db.add(answer)
        await db.commit()
        await judge_answer(db, {"message_id": str(answer.id), "chunks": [[str(doc.id), 1, 0], [str(doc.id), 1, 1]]})
        assert (await db.execute(select(AnswerEval))).first() is None
