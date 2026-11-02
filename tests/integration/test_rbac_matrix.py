"""Every protected route × every role (user session + API key) → expected allow/deny (OWASP API5: BFLA)."""

import uuid
from typing import Any

import httpx
import pytest

from rag.auth.rbac import Role
from tests.integration.conftest import Account, add_member

Z = uuid.UUID(int=0)
ADMIN, EDITOR, VIEWER = Role.ADMIN, Role.EDITOR, Role.VIEWER
ALL = {ADMIN, EDITOR, VIEWER}
NONE: set[Role] = set()
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf"
    b"\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)

# (method, path, body_or_file, allowed_user_roles, allowed_apikey_roles). "Allowed" = anything except 401/403.
MATRIX: list[tuple[str, str, Any, set[Role], set[Role]]] = [
    ("GET", "/v1/me", None, ALL, ALL),
    ("PATCH", "/v1/me", {"name": "Test User"}, ALL, NONE),
    ("POST", "/v1/chat", {"message": "test", "conversation_id": str(Z)}, ALL, NONE),
    ("GET", "/v1/chat-models", None, ALL, NONE),
    ("POST", "/v1/model-connections", {"provider": "ollama", "model": "test:latest"}, ALL, NONE),
    ("DELETE", f"/v1/model-connections/{Z}", None, ALL, NONE),
    ("GET", "/v1/documents", None, ALL, ALL),
    ("POST", "/v1/documents", ("file", ("a.txt", b"hello", "text/plain")), {ADMIN, EDITOR}, {ADMIN, EDITOR}),
    ("GET", f"/v1/documents/{Z}", None, ALL, ALL),
    ("DELETE", f"/v1/documents/{Z}", None, {ADMIN, EDITOR}, {ADMIN, EDITOR}),
    ("GET", "/v1/receipts", None, ALL, ALL),
    ("POST", "/v1/receipts", ("file", ("r.png", _PNG, "image/png")), {ADMIN, EDITOR}, {ADMIN, EDITOR}),
    ("GET", f"/v1/receipts/{Z}", None, ALL, ALL),
    ("GET", "/v1/conversations", None, ALL, NONE),
    ("GET", f"/v1/conversations/{Z}", None, ALL, NONE),
    ("PATCH", f"/v1/conversations/{Z}", {"title": "t"}, ALL, NONE),
    ("DELETE", f"/v1/conversations/{Z}", None, ALL, NONE),
    ("POST", f"/v1/messages/{Z}/feedback", {"rating": 1}, ALL, NONE),
    ("GET", "/v1/members", None, {ADMIN}, NONE),
    ("PATCH", f"/v1/members/{Z}", {"role": "viewer"}, {ADMIN}, NONE),
    ("DELETE", f"/v1/members/{Z}", None, {ADMIN}, NONE),
    ("POST", "/v1/invitations", {"email": "x@example.com", "role": "viewer"}, {ADMIN}, NONE),
    ("GET", "/v1/invitations", None, {ADMIN}, NONE),
    ("DELETE", f"/v1/invitations/{Z}", None, {ADMIN}, NONE),
    ("GET", "/v1/api-keys", None, {ADMIN}, NONE),
    ("POST", "/v1/api-keys", {"name": "k", "role": "viewer"}, {ADMIN}, NONE),
    ("DELETE", f"/v1/api-keys/{Z}", None, {ADMIN}, NONE),
    ("GET", "/v1/audit-log", None, {ADMIN}, {ADMIN}),
    ("POST", "/v1/auth/switch-tenant", "__TENANT__", ALL, NONE),
    (
        "POST",
        "/v1/auth/password/change",
        {"current_password": "wrong-current-password", "new_password": "a-valid-new-passphrase-2026"},
        ALL,
        NONE,
    ),
    ("POST", "/v1/auth/logout", {}, ALL, NONE),
]


async def _send(
    client: httpx.AsyncClient, method: str, path: str, body: Any, headers: dict[str, str], tenant_id: uuid.UUID
) -> httpx.Response:
    if isinstance(body, tuple):
        field, file_spec = body
        return await client.request(method, path, files={field: file_spec}, headers=headers)
    payload = {"tenant_id": str(tenant_id)} if body == "__TENANT__" else body
    return await client.request(method, path, json=payload, headers=headers)


@pytest.mark.parametrize("role", [ADMIN, EDITOR, VIEWER])
async def test_matrix(client: httpx.AsyncClient, tenant: Account, role: Role) -> None:
    actor = tenant if role == ADMIN else await add_member(client, tenant, role)
    failures = []
    for method, path, body, allowed, _ in MATRIX:
        r = await _send(client, method, path, body, actor.headers, tenant.tenant_id)
        assert r.status_code < 500, f"{role} {method} {path}: {r.text}"
        denied = r.status_code in (401, 403)
        if denied == (role in allowed):
            failures.append(f"{role} {method} {path} -> {r.status_code}")
    assert failures == []


@pytest.mark.parametrize("role", [ADMIN, EDITOR, VIEWER])
async def test_api_key_matrix(client: httpx.AsyncClient, tenant: Account, role: Role) -> None:
    created = await client.post(
        "/v1/api-keys", json={"name": f"key-{role.value}", "role": role.value}, headers=tenant.headers
    )
    assert created.status_code == 201
    headers = {"X-API-Key": created.json()["api_key"]}
    failures = []
    for method, path, body, _, allowed_apikey in MATRIX:
        r = await _send(client, method, path, body, headers, tenant.tenant_id)
        assert r.status_code < 500, f"apikey:{role} {method} {path}: {r.text}"
        denied = r.status_code in (401, 403)
        if denied == (role in allowed_apikey):
            failures.append(f"apikey:{role} {method} {path} -> {r.status_code}")
    assert failures == []


async def test_upload_denied_for_viewer(client: httpx.AsyncClient, tenant: Account) -> None:
    viewer = await add_member(client, tenant, VIEWER)
    r = await client.post("/v1/documents", files={"file": ("a.txt", b"hello", "text/plain")}, headers=viewer.headers)
    assert r.status_code == 403


@pytest.mark.parametrize(("method", "path"), [(m, p) for m, p, _, _, _ in MATRIX])
async def test_unauthenticated_denied(client: httpx.AsyncClient, method: str, path: str) -> None:
    r = await client.request(method, path, json={})
    assert r.status_code == 401
