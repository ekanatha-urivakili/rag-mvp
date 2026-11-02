import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from rag.adapters.llm.connections import resolve_chat_model
from rag.adapters.llm.router import get_router
from rag.api.routers import chat
from rag.auth.rbac import Role
from rag.core.config import get_settings
from rag.db.models import Conversation, Message, ModelConnection
from rag.db.session import get_sessionmaker
from rag.domain.models import RequestContext
from tests.integration.conftest import Account, add_member, make_tenant


async def test_personal_keys_are_write_only_encrypted_and_owner_scoped(
    client: httpx.AsyncClient,
    tenant: Account,
) -> None:
    secret = "test-private-provider-key"
    added = await client.post(
        "/v1/model-connections",
        headers=tenant.headers,
        json={
            "provider": "openai",
            "model": "test-model",
            "api_key": secret,
        },
    )
    assert added.status_code == 201, added.text
    connection_id = added.json()["id"]
    assert set(added.json()) == {"id", "provider", "model", "source", "reasoning_efforts"}
    listed = await client.get("/v1/chat-models", headers=tenant.headers)
    assert connection_id in {item["id"] for item in listed.json()["items"]}
    assert secret not in listed.text
    async with get_sessionmaker()() as db:
        row = await db.get(ModelConnection, uuid.UUID(connection_id))
        assert row is not None and row.encrypted_api_key and secret not in row.encrypted_api_key
        ctx = RequestContext(user_id=tenant.user_id, tenant_id=tenant.tenant_id, role=Role.ADMIN)
        resolved = await resolve_chat_model(db, ctx, connection_id)
        assert resolved.api_key == SecretStr(secret)

    member = await add_member(client, tenant, Role.VIEWER)
    outsider = await make_tenant(client, "other-model-workspace")
    for actor in (member, outsider):
        items = (await client.get("/v1/chat-models", headers=actor.headers)).json()["items"]
        assert connection_id not in {item["id"] for item in items}
        denied = await client.post(
            "/v1/chat", headers=actor.headers, json={"message": "test", "model_id": connection_id}
        )
        assert denied.status_code == 404
        assert (await client.delete(f"/v1/model-connections/{connection_id}", headers=actor.headers)).status_code == 404
    assert (await client.delete(f"/v1/model-connections/{connection_id}", headers=tenant.headers)).status_code == 204
    async with get_sessionmaker()() as db:
        assert await db.get(ModelConnection, uuid.UUID(connection_id)) is None
        assert await db.scalar(select(func.count()).select_from(Message)) == 0


async def test_selected_model_is_used_for_every_chat_stage_and_remembered(
    client: httpx.AsyncClient,
    tenant: Account,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    added = await client.post(
        "/v1/model-connections",
        headers=tenant.headers,
        json={
            "provider": "openai",
            "model": "selected-test-model",
            "api_key": "test-personal-key",
        },
    )
    assert added.status_code == 201
    model_id = added.json()["id"]
    calls: list[str] = []

    async def run(*args: object) -> AsyncIterator[str]:
        for purpose in ("orchestration", "generation"):
            llm = get_router().for_purpose(purpose)
            assert llm.provider == "openai" and llm.model == "selected-test-model"
            assert len(llm._entries) == 1
            calls.append(purpose)
        yield 'event: token\ndata: {"text":"OK"}\n\n'

    monkeypatch.setattr(chat, "_run", run)
    first = await client.post("/v1/chat", headers=tenant.headers, json={"message": "hello", "model_id": model_id})
    assert first.status_code == 200 and '"text":"OK"' in first.text
    async with get_sessionmaker()() as db:
        conv = await db.scalar(select(Conversation).where(Conversation.user_id == tenant.user_id))
        assert conv is not None and conv.model_id == model_id
        conversation_id = str(conv.id)
    detail = await client.get(f"/v1/conversations/{conversation_id}", headers=tenant.headers)
    assert detail.json()["model_id"] == model_id
    again = await client.post(
        "/v1/chat", headers=tenant.headers, json={"message": "again", "conversation_id": conversation_id}
    )
    assert again.status_code == 200
    assert calls == ["orchestration", "generation"] * 2


async def test_server_openai_is_default_when_configured(
    client: httpx.AsyncClient,
    tenant: Account,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr("test-server-key"))
    listing = (await client.get("/v1/chat-models", headers=tenant.headers)).json()
    assert listing["default_id"] == "server-openai"
    async with get_sessionmaker()() as db:
        selection = await resolve_chat_model(
            db,
            RequestContext(
                user_id=tenant.user_id,
                tenant_id=tenant.tenant_id,
                role=Role.ADMIN,
            ),
            None,
        )
        assert selection.entry.provider == "openai"


async def test_unknown_model_is_rejected_before_saving_chat(client: httpx.AsyncClient, tenant: Account) -> None:
    response = await client.post(
        "/v1/chat", headers=tenant.headers, json={"message": "test", "model_id": str(uuid.uuid4())}
    )
    assert response.status_code == 404
    async with get_sessionmaker()() as db:
        assert await db.scalar(select(func.count()).select_from(Conversation)) == 0


async def test_effort_is_applied_remembered_and_cleared_when_switching_models(
    client: httpx.AsyncClient,
    tenant: Account,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    added = await client.post(
        "/v1/model-connections",
        headers=tenant.headers,
        json={
            "provider": "openai",
            "model": "gpt-6-luna",
            "api_key": "test-personal-key",
        },
    )
    assert added.status_code == 201
    assert added.json()["reasoning_efforts"] == ["low", "medium", "high", "xhigh", "max"]
    seen: list[str | None] = []

    async def run(*args: object) -> AsyncIterator[str]:
        for purpose in ("orchestration", "generation"):
            seen.append(get_router().for_purpose(purpose)._entries[0][0].options.get("reasoning_effort"))
        yield 'event: token\ndata: {"text":"OK"}\n\n'

    monkeypatch.setattr(chat, "_run", run)
    first = await client.post(
        "/v1/chat",
        headers=tenant.headers,
        json={
            "message": "test",
            "model_id": added.json()["id"],
            "reasoning_effort": "high",
        },
    )
    assert first.status_code == 200
    async with get_sessionmaker()() as db:
        conv = await db.scalar(select(Conversation).where(Conversation.user_id == tenant.user_id))
        assert conv is not None and conv.reasoning_effort == "high"
        conversation_id = str(conv.id)
    detail = await client.get(f"/v1/conversations/{conversation_id}", headers=tenant.headers)
    assert detail.json()["reasoning_effort"] == "high"
    again = await client.post(
        "/v1/chat", headers=tenant.headers, json={"message": "again", "conversation_id": conversation_id}
    )
    assert again.status_code == 200
    changed = await client.post(
        "/v1/chat",
        headers=tenant.headers,
        json={
            "message": "local",
            "conversation_id": conversation_id,
            "model_id": "server-ollama",
        },
    )
    assert changed.status_code == 200
    assert seen == ["high", "high", "high", "high", None, None]
    async with get_sessionmaker()() as db:
        conv = await db.get(Conversation, uuid.UUID(conversation_id))
        assert conv is not None and conv.reasoning_effort is None


async def test_unsupported_effort_is_rejected_before_saving_chat(client: httpx.AsyncClient, tenant: Account) -> None:
    result = await client.post(
        "/v1/chat",
        headers=tenant.headers,
        json={
            "message": "test",
            "model_id": "server-ollama",
            "reasoning_effort": "high",
        },
    )
    assert result.status_code == 400
    async with get_sessionmaker()() as db:
        assert await db.scalar(select(func.count()).select_from(Conversation)) == 0
