"""Chat history: recency ordering, keyset paging, search, rename, delete, continuation, owner-only access."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import httpx
import pytest
from sqlalchemy import select

from rag.api.routers import chat as chat_router
from rag.auth.rbac import Role
from rag.db.models import Conversation, Feedback, Message
from rag.db.session import get_sessionmaker
from tests.integration.conftest import Account, add_member

T0 = datetime(2026, 1, 1, tzinfo=UTC)


async def _seed(owner: Account, title: str, minutes: int, messages: int = 2) -> uuid.UUID:
    async with get_sessionmaker()() as db:
        conv = Conversation(
            id=uuid.uuid4(),
            tenant_id=owner.tenant_id,
            user_id=owner.user_id,
            title=title,
            created_at=T0,
            updated_at=T0 + timedelta(minutes=minutes),
        )
        db.add(conv)
        await db.flush()
        for i in range(messages):
            db.add(
                Message(
                    conversation_id=conv.id,
                    role="user" if i % 2 == 0 else "assistant",
                    content=f"m{i}",
                    citations=[],
                    created_at=T0 + timedelta(seconds=i),
                )
            )
        await db.commit()
        return conv.id


async def test_history_is_ordered_by_last_activity_and_pages(client: httpx.AsyncClient, tenant: Account) -> None:
    ids = [await _seed(tenant, f"chat {i}", minutes=i) for i in range(5)]

    first = (await client.get("/v1/conversations", params={"limit": 2}, headers=tenant.headers)).json()
    assert [c["id"] for c in first] == [str(ids[4]), str(ids[3])]

    last = first[-1]
    params = {"limit": 10, "before": last["updated_at"], "before_id": last["id"]}
    rest = (await client.get("/v1/conversations", params=params, headers=tenant.headers)).json()
    assert [c["id"] for c in rest] == [str(ids[2]), str(ids[1]), str(ids[0])]


async def test_search_matches_title_and_escapes_wildcards(client: httpx.AsyncClient, tenant: Account) -> None:
    await _seed(tenant, "Quarterly budget", 1)
    await _seed(tenant, "100% coverage", 2)
    await _seed(tenant, "Holiday plans", 3)

    def titles(r: httpx.Response) -> list[str]:
        return [c["title"] for c in r.json()]

    r = await client.get("/v1/conversations", params={"q": "BUDGET"}, headers=tenant.headers)
    assert titles(r) == ["Quarterly budget"]
    r = await client.get("/v1/conversations", params={"q": "%"}, headers=tenant.headers)
    assert titles(r) == ["100% coverage"]


async def test_rename_and_delete(client: httpx.AsyncClient, tenant: Account) -> None:
    conv_id = await _seed(tenant, "old", 1)
    async with get_sessionmaker()() as db:
        msg_id = (await db.execute(select(Message.id).where(Message.role == "assistant"))).scalar_one()
    assert (
        await client.post(f"/v1/messages/{msg_id}/feedback", json={"rating": 1}, headers=tenant.headers)
    ).status_code == 204

    r = await client.patch(f"/v1/conversations/{conv_id}", json={"title": "  Renamed  "}, headers=tenant.headers)
    assert r.status_code == 200 and r.json()["title"] == "Renamed"
    r = await client.patch(f"/v1/conversations/{conv_id}", json={"title": ""}, headers=tenant.headers)
    assert r.status_code == 422
    smuggled = {"title": "x", "user_id": str(uuid.uuid4())}
    r = await client.patch(f"/v1/conversations/{conv_id}", json=smuggled, headers=tenant.headers)
    assert r.status_code == 422  # mass assignment rejected

    assert (await client.delete(f"/v1/conversations/{conv_id}", headers=tenant.headers)).status_code == 204
    assert (await client.get(f"/v1/conversations/{conv_id}", headers=tenant.headers)).status_code == 404
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(Message.id).where(Message.conversation_id == conv_id))).first() is None
        assert (await db.execute(select(Feedback.message_id))).first() is None


async def test_detail_returns_latest_window_and_loads_earlier(client: httpx.AsyncClient, tenant: Account) -> None:
    conv_id = await _seed(tenant, "long", 1, messages=7)

    page = (await client.get(f"/v1/conversations/{conv_id}", params={"limit": 3}, headers=tenant.headers)).json()
    assert [m["content"] for m in page["messages"]] == ["m4", "m5", "m6"]
    assert page["has_more"] is True

    params = {"limit": 10, "before": page["messages"][0]["created_at"]}
    older = (await client.get(f"/v1/conversations/{conv_id}", params=params, headers=tenant.headers)).json()
    assert [m["content"] for m in older["messages"]] == ["m0", "m1", "m2", "m3"]
    assert older["has_more"] is False


class _StubGraph:
    """Answers without models; records the history the graph was given."""

    seen_history: ClassVar[list[str]] = []

    async def astream(self, state: dict[str, Any], **_: Any) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        _StubGraph.seen_history = [t.content for t in state["history"]]
        yield "values", {**state, "answer": "stub answer", "citations": [], "models_used": []}


async def test_continuing_a_conversation_uses_its_history_and_moves_it_to_the_top(
    client: httpx.AsyncClient, tenant: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chat_router, "build_graph", _StubGraph)
    old = await _seed(tenant, "old", 1)
    await _seed(tenant, "newer", 2)

    body = {"message": "follow-up", "conversation_id": str(old)}
    async with client.stream("POST", "/v1/chat", json=body, headers=tenant.headers) as r:
        assert r.status_code == 200
        events = (await r.aread()).decode()
    assert f'"conversation_id": "{old}"' in events
    assert _StubGraph.seen_history == ["m0", "m1"]

    listed = (await client.get("/v1/conversations", headers=tenant.headers)).json()
    assert listed[0]["id"] == str(old)
    detail = (await client.get(f"/v1/conversations/{old}", headers=tenant.headers)).json()
    assert [m["content"] for m in detail["messages"]] == ["m0", "m1", "follow-up", "stub answer"]


async def test_other_users_cannot_rename_delete_or_continue(client: httpx.AsyncClient, tenant: Account) -> None:
    viewer = await add_member(client, tenant, Role.VIEWER)
    conv_id = await _seed(viewer, "private", 1)

    # The tenant admin is just another user here: 404, same as a missing ID (BOLA).
    assert (
        await client.patch(f"/v1/conversations/{conv_id}", json={"title": "pwned"}, headers=tenant.headers)
    ).status_code == 404
    assert (await client.delete(f"/v1/conversations/{conv_id}", headers=tenant.headers)).status_code == 404
    r = await client.post("/v1/chat", json={"message": "hi", "conversation_id": str(conv_id)}, headers=tenant.headers)
    assert r.status_code == 404
    assert (await client.get("/v1/conversations", params={"q": "private"}, headers=tenant.headers)).json() == []

    detail = (await client.get(f"/v1/conversations/{conv_id}", headers=viewer.headers)).json()
    assert detail["title"] == "private" and len(detail["messages"]) == 2
