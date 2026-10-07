import uuid
from datetime import UTC, datetime
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import select

from rag.api.routers import chat
from rag.auth.rbac import Role
from rag.core.config import get_settings
from rag.db.models import Conversation, Document, Job, Message
from rag.db.session import get_sessionmaker
from rag.ingestion.pipeline import on_ingest_failed
from tests.integration.conftest import PASSWORD, Account, add_member


async def test_cookie_refresh_requires_allowed_origin_and_uses_configured_name(
    client: httpx.AsyncClient, tenant: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "refresh_cookie_name", "custom_refresh")
    response = await client.post("/v1/auth/login", json={"email": tenant.email, "password": PASSWORD})
    assert "custom_refresh=" in response.headers["set-cookie"]
    for headers in ({}, {"Origin": "http://evil.localhost:8501"}, {"Origin": "null"}):
        denied = await client.post("/v1/auth/refresh", json={}, headers=headers)
        assert denied.status_code == 403
    allowed = await client.post("/v1/auth/refresh", json={}, headers={"Origin": settings.cors_origins[0]})
    assert allowed.status_code == 200


async def test_refresh_rate_limit_is_persistent(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "rl_refresh_per_ip", 2)
    for _ in range(2):
        assert (await client.post("/v1/auth/refresh", json={"refresh_token": "invalid"})).status_code == 401
    response = await client.post("/v1/auth/refresh", json={"refresh_token": "invalid"})
    assert response.status_code == 429 and int(response.headers["retry-after"]) > 0


async def test_ingestion_error_is_not_returned_or_emailed(client: httpx.AsyncClient, tenant: Account) -> None:
    response = await client.post("/v1/documents", files={"file": ("bad.txt", b"content")}, headers=tenant.headers)
    document_id = uuid.UUID(response.json()["document_id"])
    async with get_sessionmaker()() as db:
        await on_ingest_failed(db, {"document_id": str(document_id), "version": 1}, "secret provider response")
        jobs = (await db.execute(select(Job).where(Job.type == "send_email"))).scalars()
        assert all("secret provider response" not in str(job.payload) for job in jobs)
    visible = await client.get(f"/v1/documents/{document_id}", headers=tenant.headers)
    assert visible.json()["status"] == "failed" and "secret provider response" not in visible.text
    async with get_sessionmaker()() as db:
        document = await db.get(Document, document_id)
        assert document
        document.error = "older stored secret provider response"
        await db.commit()
    for endpoint in (f"/v1/documents/{document_id}", "/v1/documents"):
        visible = await client.get(endpoint, headers=tenant.headers)
        assert visible.status_code == 200 and "secret provider response" not in visible.text


async def test_message_cursor_keeps_equal_timestamps(client: httpx.AsyncClient, tenant: Account) -> None:
    conversation_id = uuid.uuid4()
    timestamp = datetime.now(UTC)
    ids = [uuid.UUID(int=i) for i in range(1, 6)]
    async with get_sessionmaker()() as db:
        db.add(Conversation(id=conversation_id, tenant_id=tenant.tenant_id, user_id=tenant.user_id, title="ties"))
        await db.flush()
        db.add_all(
            Message(id=id_, conversation_id=conversation_id, role="user", content=str(id_), created_at=timestamp)
            for id_ in ids
        )
        await db.commit()
    seen = []
    params: dict[str, str | int] = {"limit": 2}
    while True:
        response = await client.get(f"/v1/conversations/{conversation_id}", params=params, headers=tenant.headers)
        detail = response.json()
        seen.extend(m["id"] for m in detail["messages"])
        if not detail["has_more"]:
            break
        oldest = detail["messages"][0]
        params.update(before=oldest["created_at"], before_id=oldest["id"])
    assert sorted(seen) == sorted(str(id_) for id_ in ids)


async def test_deletion_during_generation_emits_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def values(*args, **kwargs):
        yield "values", {"answer": "done"}

    monkeypatch.setattr(chat, "build_graph", lambda: Mock(astream=values))
    events = [event async for event in chat._run({}, uuid.uuid4(), "trace")]
    assert len(events) == 1 and '"code": "not_found"' in events[0]


async def test_admin_lists_are_bounded_and_paginated(client: httpx.AsyncClient, tenant: Account) -> None:
    for i in range(3):
        await add_member(client, tenant, Role.VIEWER)
        assert (
            await client.post("/v1/api-keys", json={"name": f"key{i}", "role": "viewer"}, headers=tenant.headers)
        ).status_code == 201
        assert (
            await client.post(
                "/v1/invitations", json={"email": f"invite{i}@example.com", "role": "viewer"}, headers=tenant.headers
            )
        ).status_code == 202
    for endpoint in ("members", "api-keys", "invitations"):
        first = await client.get(f"/v1/{endpoint}", params={"limit": 1}, headers=tenant.headers)
        second = await client.get(f"/v1/{endpoint}", params={"limit": 1, "offset": 1}, headers=tenant.headers)
        assert first.status_code == second.status_code == 200
        assert len(first.json()) == len(second.json()) == 1 and first.json() != second.json()
        assert (await client.get(f"/v1/{endpoint}", params={"limit": 101}, headers=tenant.headers)).status_code == 422


async def test_invitation_email_quota(
    client: httpx.AsyncClient, tenant: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rl_invite_per_tenant", 2)
    for i in range(2):
        assert (
            await client.post(
                "/v1/invitations", json={"email": f"invite{i}@example.com", "role": "viewer"}, headers=tenant.headers
            )
        ).status_code == 202
    response = await client.post(
        "/v1/invitations", json={"email": "overflow@example.com", "role": "viewer"}, headers=tenant.headers
    )
    assert response.status_code == 429 and "retry-after" in response.headers
    async with get_sessionmaker()() as db:
        jobs = list((await db.execute(select(Job).where(Job.type == "send_email"))).scalars())
        assert len(jobs) == 2
