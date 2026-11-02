"""Rolling conversation summary (§4.10): queued when the window slides, folded by the worker, fed to the next turn."""

import uuid
from collections.abc import AsyncIterator
from contextlib import nullcontext
from typing import Any, ClassVar

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from rag.adapters.llm.base import Completion, Message, RouteEntry, Usage
from rag.api.routers import chat as chat_router
from rag.core.config import get_settings
from rag.db.models import Conversation, Job
from rag.db.session import get_sessionmaker
from rag.graph import summary as summary_mod
from tests.integration.conftest import Account
from tests.integration.test_conversations import _seed


class _Graph:
    seen: ClassVar[dict[str, Any]] = {}

    async def astream(self, state: dict[str, Any], **_: Any) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        _Graph.seen = state
        yield "values", {**state, "answer": "ok", "citations": [], "models_used": []}


class _Router:
    calls: ClassVar[list[str]] = []

    def select_chat(self, entry: RouteEntry, api_key: SecretStr | None = None) -> nullcontext[None]:
        return nullcontext()

    def for_purpose(self, purpose: str, on_attempt: Any = None) -> "_Router":
        assert purpose == "orchestration"
        return self

    async def complete(self, messages: list[Message], **_: Any) -> Completion:
        _Router.calls.append(messages[-1].content)
        return Completion("User asked about m0..m2.", None, Usage(1, 1), 1, "fake", "fake-m")


async def _chat(client: httpx.AsyncClient, tenant: Account, conv_id: uuid.UUID, text: str) -> None:
    body = {"message": text, "conversation_id": str(conv_id)}
    async with client.stream("POST", "/v1/chat", json=body, headers=tenant.headers) as r:
        assert r.status_code == 200
        await r.aread()


async def _jobs() -> list[Job]:
    async with get_sessionmaker()() as db:
        return list((await db.execute(select(Job).where(Job.type == "summarize_conversation"))).scalars())


async def test_summary_folds_messages_outside_the_window(
    client: httpx.AsyncClient, tenant: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "history_turns", 4)
    monkeypatch.setattr(chat_router, "build_graph", _Graph)
    monkeypatch.setattr(summary_mod, "get_router", _Router)
    _Router.calls = []

    conv_id = await _seed(tenant, "short", 1, messages=2)
    await _chat(client, tenant, conv_id, "q")  # 4 messages: everything still fits the window
    assert await _jobs() == []

    await _chat(client, tenant, conv_id, "q2")  # 6 messages: m0 and m1 slid out
    jobs = await _jobs()
    assert [j.payload for j in jobs] == [{"conversation_id": str(conv_id)}]

    async with get_sessionmaker()() as db:
        await summary_mod.summarize_conversation(db, jobs[0].payload)
        await summary_mod.summarize_conversation(db, jobs[0].payload)  # idempotent: nothing new to fold
        conv = await db.get(Conversation, conv_id)
        assert conv is not None
        assert (conv.summary, conv.summarized_count) == ("User asked about m0..m2.", 2)
    assert len(_Router.calls) == 1 and "m0" in _Router.calls[0] and "m1" in _Router.calls[0]
    assert "q2" not in _Router.calls[0]  # still inside the window

    await _chat(client, tenant, conv_id, "q3")
    assert _Graph.seen["summary"] == "User asked about m0..m2."
    assert [t.content for t in _Graph.seen["history"]] == ["q", "ok", "q2", "ok"]


async def test_summary_job_ignores_deleted_conversation() -> None:
    async with get_sessionmaker()() as db:
        await summary_mod.summarize_conversation(db, {"conversation_id": str(uuid.uuid4())})
