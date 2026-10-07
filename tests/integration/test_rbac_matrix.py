"""Every protected route × every role → expected allow/deny (OWASP API5: BFLA)."""

import uuid

import httpx
import pytest

from rag.auth.rbac import Role
from tests.integration.conftest import Account, add_member

Z = uuid.UUID(int=0)
ADMIN, EDITOR, VIEWER = Role.ADMIN, Role.EDITOR, Role.VIEWER
ALL = {ADMIN, EDITOR, VIEWER}

# (method, path, json, allowed roles). "Allowed" = anything except 401/403.
MATRIX = [
    ("GET", "/v1/me", None, ALL),
    ("POST", "/v1/chat", {"message": "test", "conversation_id": str(Z)}, ALL),
    ("GET", "/v1/documents", None, ALL),
    ("GET", f"/v1/documents/{Z}", None, ALL),
    ("DELETE", f"/v1/documents/{Z}", None, {ADMIN, EDITOR}),
    ("GET", "/v1/receipts", None, ALL),
    ("GET", f"/v1/receipts/{Z}", None, ALL),
    ("GET", "/v1/conversations", None, ALL),
    ("GET", f"/v1/conversations/{Z}", None, ALL),
    ("PATCH", f"/v1/conversations/{Z}", {"title": "t"}, ALL),
    ("DELETE", f"/v1/conversations/{Z}", None, ALL),
    ("POST", f"/v1/messages/{Z}/feedback", {"rating": 1}, ALL),
    ("GET", "/v1/members", None, {ADMIN}),
    ("PATCH", f"/v1/members/{Z}", {"role": "viewer"}, {ADMIN}),
    ("DELETE", f"/v1/members/{Z}", None, {ADMIN}),
    ("POST", "/v1/invitations", {"email": "x@example.com", "role": "viewer"}, {ADMIN}),
    ("GET", "/v1/invitations", None, {ADMIN}),
    ("GET", "/v1/api-keys", None, {ADMIN}),
    ("POST", "/v1/api-keys", {"name": "k", "role": "viewer"}, {ADMIN}),
    ("DELETE", f"/v1/api-keys/{Z}", None, {ADMIN}),
    ("GET", "/v1/audit-log", None, {ADMIN}),
    ("POST", "/v1/auth/logout", {}, ALL),
]


@pytest.mark.parametrize("role", [ADMIN, EDITOR, VIEWER])
async def test_matrix(client: httpx.AsyncClient, tenant: Account, role: Role) -> None:
    actor = tenant if role == ADMIN else await add_member(client, tenant, role)
    failures = []
    for method, path, body, allowed in MATRIX:
        r = await client.request(method, path, json=body, headers=actor.headers)
        assert r.status_code < 500, f"{role} {method} {path}: {r.text}"
        denied = r.status_code in (401, 403)
        if denied == (role in allowed):
            failures.append(f"{role} {method} {path} -> {r.status_code}")
    assert failures == []


async def test_upload_denied_for_viewer(client: httpx.AsyncClient, tenant: Account) -> None:
    viewer = await add_member(client, tenant, VIEWER)
    r = await client.post("/v1/documents", files={"file": ("a.txt", b"hello", "text/plain")}, headers=viewer.headers)
    assert r.status_code == 403


@pytest.mark.parametrize(("method", "path"), [(m, p) for m, p, _, _ in MATRIX] + [("POST", "/v1/chat")])
async def test_unauthenticated_denied(client: httpx.AsyncClient, method: str, path: str) -> None:
    r = await client.request(method, path, json={})
    assert r.status_code == 401
