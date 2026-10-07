import re

import httpx

from rag.auth.rbac import Role
from rag.worker.loop import run_once
from tests.integration.conftest import PASSWORD, Account, add_member, login, mailpit_body, mailpit_messages


async def test_login_failures_are_indistinguishable(client: httpx.AsyncClient, tenant: Account) -> None:
    wrong_pw = await client.post("/v1/auth/login", json={"email": tenant.email, "password": "nope-nope-nope"})
    no_user = await client.post("/v1/auth/login", json={"email": "ghost@example.com", "password": "nope-nope-nope"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json()["error"]["message"] == no_user.json()["error"]["message"]


async def test_validation_errors_do_not_echo_input(client: httpx.AsyncClient) -> None:
    r = await client.post("/v1/auth/login", json={"email": "x", "password": "SuperSecret!", "is_admin": True})
    assert r.status_code == 422
    assert "SuperSecret" not in r.text


async def test_login_rate_limited(client: httpx.AsyncClient, tenant: Account) -> None:
    # The fixture already logged in once; the per-email limit is 5 attempts per window.
    codes = [
        (await client.post("/v1/auth/login", json={"email": tenant.email, "password": "wrong-password-1"})).status_code
        for _ in range(6)
    ]
    assert codes[:4] == [401] * 4 and codes[4:] == [429, 429]
    # Even the correct password is refused while limited.
    r = await client.post("/v1/auth/login", json={"email": tenant.email, "password": PASSWORD})
    assert r.status_code == 429 and "retry-after" in r.headers


async def test_refresh_rotation_and_reuse_detection(client: httpx.AsyncClient, tenant: Account) -> None:
    r = await client.post("/v1/auth/login", json={"email": tenant.email, "password": PASSWORD})
    rt1 = r.json()["refresh_token"]
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()

    r2 = await client.post("/v1/auth/refresh", json={"refresh_token": rt1})
    assert r2.status_code == 200
    rt2 = r2.json()["refresh_token"]
    assert rt2 != rt1

    # Replaying the rotated token revokes the whole family — including the fresh one.
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": rt1})).status_code == 401
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": rt2})).status_code == 401
    for access in (r.json()["access_token"], r2.json()["access_token"]):
        assert (await client.get("/v1/me", headers={"Authorization": f"Bearer {access}"})).status_code == 401


async def test_security_headers(client: httpx.AsyncClient, tenant: Account) -> None:
    r = await client.get("/v1/me", headers=tenant.headers)
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    assert "server" not in {k.lower() for k in r.headers if k.lower() == "server" and "uvicorn" in r.headers[k]}


async def test_untrusted_host_rejected(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz", headers={"Host": "evil.example"})
    assert r.status_code == 400


async def test_invite_accept_login_via_mailpit(client: httpx.AsyncClient, tenant: Account) -> None:
    email = "newbie-invite@example.com"
    r = await client.post("/v1/invitations", json={"email": email, "role": "editor"}, headers=tenant.headers)
    assert r.status_code == 202
    while await run_once():
        pass

    msgs = await mailpit_messages(email)
    assert msgs, "invite email not delivered to Mailpit"
    body = await mailpit_body(str(msgs[0]["ID"]))
    token = re.search(r"token=([\w\-]+)", body)
    assert token

    weak = await client.post("/v1/invitations/accept", json={"token": token[1], "password": "short"})
    assert weak.status_code == 400  # weak password rejected, token NOT consumed
    ok = await client.post("/v1/invitations/accept", json={"token": token[1], "password": PASSWORD})
    assert ok.status_code == 200
    again = await client.post("/v1/invitations/accept", json={"token": token[1], "password": PASSWORD})
    assert again.status_code == 400  # single use

    me = (await client.get("/v1/me", headers={"Authorization": f"Bearer {await login(client, email)}"})).json()
    assert me["tenant"]["role"] == "editor"


async def test_password_reset_revokes_sessions(client: httpx.AsyncClient, tenant: Account) -> None:
    user = await add_member(client, tenant, Role.VIEWER)
    old_refresh = (await client.post("/v1/auth/login", json={"email": user.email, "password": PASSWORD})).json()[
        "refresh_token"
    ]

    r = await client.post("/v1/auth/password/forgot", json={"email": user.email})
    assert r.status_code == 202
    unknown = await client.post("/v1/auth/password/forgot", json={"email": "nobody@example.com"})
    assert unknown.status_code == 202 and unknown.json() == r.json()  # no enumeration
    while await run_once():
        pass

    body = await mailpit_body(str((await mailpit_messages(user.email))[0]["ID"]))
    token = re.search(r"token=([\w\-]+)", body)
    assert token
    new_pw = "a-brand-new-passphrase-2026"
    assert (
        await client.post("/v1/auth/password/reset", json={"token": token[1], "new_password": new_pw})
    ).status_code == 204

    assert (await client.get("/v1/me", headers=user.headers)).status_code == 401  # old access token revoked
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": old_refresh})).status_code == 401
    await login(client, user.email, new_pw)


async def test_removed_member_loses_access_immediately(client: httpx.AsyncClient, tenant: Account) -> None:
    editor = await add_member(client, tenant, Role.EDITOR)
    assert (await client.get("/v1/documents", headers=editor.headers)).status_code == 200
    assert (await client.delete(f"/v1/members/{editor.user_id}", headers=tenant.headers)).status_code == 204
    assert (await client.get("/v1/documents", headers=editor.headers)).status_code == 401


async def test_last_admin_guardrails(client: httpx.AsyncClient, tenant: Account) -> None:
    other_admin = await add_member(client, tenant, Role.ADMIN)
    # Users cannot change their own role.
    r = await client.patch(f"/v1/members/{tenant.user_id}", json={"role": "viewer"}, headers=tenant.headers)
    assert r.status_code == 403
    # Demote the other admin → tenant.admin is now the last admin and cannot be demoted by anyone.
    r = await client.patch(f"/v1/members/{other_admin.user_id}", json={"role": "viewer"}, headers=tenant.headers)
    assert r.status_code == 204


async def test_api_key_auth(client: httpx.AsyncClient, tenant: Account) -> None:
    r = await client.post("/v1/api-keys", json={"name": "ci", "role": "viewer"}, headers=tenant.headers)
    assert r.status_code == 201
    key = r.json()["api_key"]
    assert (await client.get("/v1/documents", headers={"X-API-Key": key})).status_code == 200
    assert (
        await client.post(
            "/v1/invitations", json={"email": "a@example.com", "role": "viewer"}, headers={"X-API-Key": key}
        )
    ).status_code == 403
    listed = (await client.get("/v1/api-keys", headers=tenant.headers)).json()
    assert "api_key" not in listed[0] and "key_hash" not in listed[0]

    assert (await client.delete(f"/v1/api-keys/{r.json()['id']}", headers=tenant.headers)).status_code == 204
    assert (await client.get("/v1/documents", headers={"X-API-Key": key})).status_code == 401
    actions = [e["action"] for e in (await client.get("/v1/audit-log", headers=tenant.headers)).json()]
    assert "apikey.created" in actions and "apikey.revoked" in actions


async def test_logout_revokes_access_without_refresh_body(client: httpx.AsyncClient, tenant: Account) -> None:
    assert (await client.post("/v1/auth/logout", json={}, headers=tenant.headers)).status_code == 204
    assert (await client.get("/v1/me", headers=tenant.headers)).status_code == 401
