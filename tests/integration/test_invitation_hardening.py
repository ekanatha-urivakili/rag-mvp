import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, select

from rag.auth.rbac import Role
from rag.auth.service import purge_expired_tokens
from rag.db.models import EmailToken, Job, RefreshToken
from rag.db.session import get_sessionmaker
from tests.integration.conftest import PASSWORD, Account, add_member, make_tenant


async def _invite_token(client: httpx.AsyncClient, admin: Account, email: str, role: str = "viewer") -> str:
    r = await client.post("/v1/invitations", json={"email": email, "role": role}, headers=admin.headers)
    assert r.status_code == 202, r.text
    async with get_sessionmaker()() as db:
        job = (
            await db.execute(
                select(Job)
                .where(Job.type == "send_email", Job.payload["to"].astext == email)
                .order_by(Job.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
    return str(job.payload["context"]["link"]).split("token=", 1)[1]


async def test_existing_account_must_prove_password_to_accept(client: httpx.AsyncClient, tenant: Account) -> None:
    other = await make_tenant(client, "globex")
    existing = await add_member(client, other, Role.ADMIN)
    token = await _invite_token(client, tenant, existing.email)

    missing = await client.post("/v1/invitations/accept", json={"token": token, "password": None})
    assert missing.status_code == 400 and missing.json()["error"]["code"] == "invalid_password"
    wrong = await client.post("/v1/invitations/accept", json={"token": token, "password": "not-the-password-1"})
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "invalid_password"

    ok = await client.post("/v1/invitations/accept", json={"token": token, "password": PASSWORD})
    assert ok.status_code == 200 and ok.json()["tenant_id"] == str(tenant.tenant_id)
    replay = await client.post("/v1/invitations/accept", json={"token": token, "password": PASSWORD})
    assert replay.status_code == 400


async def test_invite_token_guessing_is_limited_per_link(client: httpx.AsyncClient, tenant: Account) -> None:
    existing = await add_member(client, await make_tenant(client, "initech"), Role.VIEWER)
    token = await _invite_token(client, tenant, existing.email)
    for _ in range(5):
        r = await client.post("/v1/invitations/accept", json={"token": token, "password": "guess-guess-guess"})
        assert r.status_code == 400
    blocked = await client.post("/v1/invitations/accept", json={"token": token, "password": PASSWORD})
    assert blocked.status_code == 429


async def test_reinvite_supersedes_earlier_link(client: httpx.AsyncClient, tenant: Account) -> None:
    email = f"twice-{uuid.uuid4().hex[:6]}@example.com"
    first = await _invite_token(client, tenant, email, "admin")
    second = await _invite_token(client, tenant, email, "viewer")

    stale = await client.post("/v1/invitations/accept", json={"token": first, "password": PASSWORD})
    assert stale.status_code == 400 and stale.json()["error"]["code"] == "invalid_token"
    pending = (await client.get("/v1/invitations", headers=tenant.headers)).json()
    assert [(i["email"], i["role"]) for i in pending] == [(email, "viewer")]
    assert (
        await client.post("/v1/invitations/accept", json={"token": second, "password": PASSWORD})
    ).status_code == 200


async def test_admin_revokes_pending_invitation(client: httpx.AsyncClient, tenant: Account) -> None:
    email = f"revoked-{uuid.uuid4().hex[:6]}@example.com"
    token = await _invite_token(client, tenant, email)
    invitation_id = (await client.get("/v1/invitations", headers=tenant.headers)).json()[0]["id"]

    viewer = await add_member(client, tenant, Role.VIEWER)
    assert (await client.delete(f"/v1/invitations/{invitation_id}", headers=viewer.headers)).status_code == 403
    outsider = await make_tenant(client, "umbrella")
    assert (await client.delete(f"/v1/invitations/{invitation_id}", headers=outsider.headers)).status_code == 404

    assert (await client.delete(f"/v1/invitations/{invitation_id}", headers=tenant.headers)).status_code == 204
    assert (await client.get("/v1/invitations", headers=tenant.headers)).json() == []
    again = await client.delete(f"/v1/invitations/{invitation_id}", headers=tenant.headers)
    assert again.status_code == 404
    accept = await client.post("/v1/invitations/accept", json={"token": token, "password": PASSWORD})
    assert accept.status_code == 400
    audit = (await client.get("/v1/audit-log", headers=tenant.headers)).json()
    assert any(e["action"] == "member.invite_revoked" for e in audit)


async def test_purge_removes_only_long_expired_tokens(client: httpx.AsyncClient, tenant: Account) -> None:
    async with get_sessionmaker()() as db:
        live_before = (await db.execute(select(func.count()).select_from(RefreshToken))).scalar_one()
        old = datetime.now(UTC) - timedelta(days=3)
        db.add(
            RefreshToken(
                id=uuid.uuid4(),
                user_id=tenant.user_id,
                tenant_id=tenant.tenant_id,
                family_id=uuid.uuid4(),
                token_hash=uuid.uuid4().hex,
                expires_at=old,
            )
        )
        db.add(EmailToken(type="password_reset", email=tenant.email, token_hash=uuid.uuid4().hex, expires_at=old))
        await db.commit()

        await purge_expired_tokens(db)
        await db.commit()
        assert (await db.execute(select(func.count()).select_from(RefreshToken))).scalar_one() == live_before
        assert (await db.execute(select(func.count()).select_from(EmailToken))).scalar_one() == 0
    me = await client.get("/v1/me", headers=tenant.headers)
    assert me.status_code == 200  # the live session survives the purge
