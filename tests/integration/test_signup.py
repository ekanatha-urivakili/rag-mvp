import re
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from rag.auth.tokens import hash_token
from rag.core.config import get_settings
from rag.db.models import EmailToken, Tenant, User
from rag.db.session import get_sessionmaker
from rag.worker.loop import run_once
from tests.integration.conftest import PASSWORD, Account, mailpit_body, mailpit_messages


async def signup_link(client: httpx.AsyncClient, email: str) -> str:
    response = await client.post("/v1/auth/signup", json={"email": email})
    assert response.status_code == 202, response.text
    while await run_once():
        pass
    body = await mailpit_body(str((await mailpit_messages(email))[0]["ID"]))
    match = re.search(r"token=([\w-]+)", body)
    assert match
    return match[1]


async def test_signup_mail_verification_login_and_isolation(client: httpx.AsyncClient, tenant: Account) -> None:
    email = f"signup-{uuid.uuid4().hex}@example.com"
    token = await signup_link(client, email)
    assert (await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})).status_code == 401
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(User).where(User.email == email))).scalar_one_or_none() is None
        stored = (await db.execute(select(EmailToken).where(EmailToken.email == email))).scalar_one()
        assert stored.token_hash == hash_token(token) and stored.token_hash != token
    body = {"token": token, "password": PASSWORD, "workspace_name": "My workspace"}
    weak = await client.post("/v1/auth/signup/verify", json={**body, "password": "short"})
    assert weak.status_code == 400
    response = await client.post("/v1/auth/signup/verify", json=body)
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    me = (await client.get("/v1/me", headers=headers)).json()
    assert me["email"] == email and me["tenant"]["role"] == "admin"
    assert me["tenant"]["id"] != str(tenant.tenant_id) and len(me["tenants"]) == 1
    assert (await client.get("/v1/documents", headers=headers)).json()["total"] == 0
    assert (
        await client.post("/v1/auth/switch-tenant", headers=headers, json={"tenant_id": str(tenant.tenant_id)})
    ).status_code == 403
    assert (await client.post("/v1/auth/signup/verify", json=body)).status_code == 400
    assert (await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})).status_code == 200


async def test_existing_signup_is_generic_and_does_not_replace_user(client: httpx.AsyncClient, tenant: Account) -> None:
    known = await client.post("/v1/auth/signup", json={"email": tenant.email})
    unknown = await client.post("/v1/auth/signup", json={"email": "new-signup@example.com"})
    assert known.status_code == unknown.status_code == 202 and known.json() == unknown.json()
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(func.count()).select_from(Tenant))).scalar_one() == 1
        assert (await db.execute(select(EmailToken).where(EmailToken.email == tenant.email))).first() is None
    while await run_once():
        pass
    [message] = await mailpit_messages(tenant.email)
    assert message["Subject"] == "You already have a RAG account"
    body = await mailpit_body(str(message["ID"]))
    assert f"{get_settings().public_ui_url}/login" in body and "token=" not in body
    assert (await client.post("/v1/auth/login", json={"email": tenant.email, "password": PASSWORD})).status_code == 200


async def test_signup_invalid_expired_and_mass_assignment(client: httpx.AsyncClient) -> None:
    body = {"token": "nonexistent-token-123456", "password": PASSWORD, "workspace_name": "test"}
    assert (await client.post("/v1/auth/signup/verify", json=body)).status_code == 400
    assert (await client.post("/v1/auth/signup", json={"email": "x@example.com", "role": "admin"})).status_code == 422
    async with get_sessionmaker()() as db:
        db.add(
            EmailToken(
                type="signup",
                email="expired@example.com",
                token_hash=hash_token(body["token"]),
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        await db.commit()
    assert (await client.post("/v1/auth/signup/verify", json=body)).status_code == 400


async def test_signup_rate_limit_and_disable(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(5):
        assert (await client.post("/v1/auth/signup", json={"email": f"user{i}@example.com"})).status_code == 202
    assert (await client.post("/v1/auth/signup", json={"email": "limited@example.com"})).status_code == 429
    monkeypatch.setattr(get_settings(), "signup_enabled", False)
    assert (await client.post("/v1/auth/signup", json={"email": "disabled@example.com"})).status_code == 403
    assert (
        await client.post(
            "/v1/auth/signup/verify", json={"token": "a" * 32, "password": PASSWORD, "workspace_name": "disabled"}
        )
    ).status_code == 403
