"""OWASP API1 (BOLA): IDs from another tenant or another user behave exactly like nonexistent IDs."""

import uuid

import httpx

from rag.auth.rbac import Role
from rag.db.models import Conversation, Message
from rag.db.session import get_sessionmaker
from tests.integration.conftest import Account, add_member, make_tenant


async def test_documents_are_tenant_scoped(client: httpx.AsyncClient, tenant: Account) -> None:
    other = await make_tenant(client, "globex")
    r = await client.post(
        "/v1/documents", files={"file": ("secret.txt", b"globex secret plans", "text/plain")}, headers=other.headers
    )
    assert r.status_code == 202, r.text
    doc_id = r.json()["document_id"]

    assert (await client.get(f"/v1/documents/{doc_id}", headers=tenant.headers)).status_code == 404
    assert (await client.delete(f"/v1/documents/{doc_id}", headers=tenant.headers)).status_code == 404
    assert (await client.get("/v1/documents", headers=tenant.headers)).json()["total"] == 0
    assert (await client.get(f"/v1/documents/{doc_id}", headers=other.headers)).status_code == 200


async def test_reupload_is_idempotent_and_new_content_bumps_version(client: httpx.AsyncClient, tenant: Account) -> None:
    files = {"file": ("guide.md", b"# Guide\n\nv1", "text/markdown")}
    first = await client.post("/v1/documents", files=files, headers=tenant.headers)
    again = await client.post("/v1/documents", files=files, headers=tenant.headers)
    assert first.status_code == 202 and again.status_code == 200
    assert first.json()["document_id"] == again.json()["document_id"]

    changed = await client.post(
        "/v1/documents", files={"file": ("guide.md", b"# Guide\n\nv2", "text/markdown")}, headers=tenant.headers
    )
    assert changed.json()["document_id"] == first.json()["document_id"]
    doc = (await client.get(f"/v1/documents/{first.json()['document_id']}", headers=tenant.headers)).json()
    assert doc["version"] == 2


async def test_upload_rejects_spoofed_type(client: httpx.AsyncClient, tenant: Account) -> None:
    r = await client.post(
        "/v1/documents",
        files={"file": ("invoice.pdf", b"MZ\x90\x00\x03\x00\x00\x00\x04", "application/pdf")},
        headers=tenant.headers,
    )
    assert r.status_code == 415


async def test_conversations_are_owner_only(client: httpx.AsyncClient, tenant: Account) -> None:
    viewer = await add_member(client, tenant, Role.VIEWER)
    async with get_sessionmaker()() as db:
        conv = Conversation(id=uuid.uuid4(), tenant_id=tenant.tenant_id, user_id=viewer.user_id, title="private")
        db.add(conv)
        await db.flush()
        msg = Message(id=uuid.uuid4(), conversation_id=conv.id, role="assistant", content="hi", citations=[])
        db.add(msg)
        await db.commit()

    assert (await client.get(f"/v1/conversations/{conv.id}", headers=viewer.headers)).status_code == 200
    # Even the tenant admin cannot read another user's chat.
    assert (await client.get(f"/v1/conversations/{conv.id}", headers=tenant.headers)).status_code == 404
    assert (await client.get("/v1/conversations", headers=tenant.headers)).json() == []
    fb = await client.post(f"/v1/messages/{msg.id}/feedback", json={"rating": 1}, headers=tenant.headers)
    assert fb.status_code == 404
    fb = await client.post(f"/v1/messages/{msg.id}/feedback", json={"rating": -1}, headers=viewer.headers)
    assert fb.status_code == 204


async def test_switch_tenant_requires_membership(client: httpx.AsyncClient, tenant: Account) -> None:
    other = await make_tenant(client, "initech")
    r = await client.post("/v1/auth/switch-tenant", json={"tenant_id": str(other.tenant_id)}, headers=tenant.headers)
    assert r.status_code == 403
