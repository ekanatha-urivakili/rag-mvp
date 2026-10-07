"""Live local Next.js/BFF/API smoke test. Creates and removes only unique temporary workspaces.

Run after starting this project: uv run python scripts/smoke_workspace.py
The receipt is seeded; real OCR/extraction is covered by tests/integration/test_receipts.py.
"""

import asyncio
import re
import secrets
import uuid
from decimal import Decimal

import httpx
from sqlalchemy import delete

from rag.auth.service import bootstrap_admin
from rag.db.models import Document, RateLimitBucket, Receipt, Tenant, User
from rag.db.session import get_sessionmaker

BASE = "http://localhost:3000"
RUN = uuid.uuid4().hex
EMAIL = f"console-admin-{RUN}@example.com"
VIEWER_EMAIL = f"console-viewer-{RUN}@example.com"
OTHER_EMAIL = f"console-other-{RUN}@example.com"
PASSWORD = secrets.token_urlsafe(32)
tenants: list[uuid.UUID] = []


async def seed() -> uuid.UUID:
    async with get_sessionmaker()() as db:
        tenant = await bootstrap_admin(db, email=EMAIL, password=PASSWORD, tenant_name=f"Console smoke {RUN}")
        tenants.append(tenant)
        other = await bootstrap_admin(db, email=OTHER_EMAIL, password=PASSWORD, tenant_name=f"Other smoke {RUN}")
        tenants.append(other)
        doc = Document(
            id=uuid.uuid4(),
            tenant_id=tenant,
            title="Smoke receipt",
            kind="receipt",
            source_uri="smoke://fixture",
            mime_type="text/plain",
            size_bytes=1,
            content_hash=RUN,
            version=1,
            status="ready",
        )
        db.add(doc)
        await db.flush()
        db.add(
            Receipt(
                document_id=doc.id,
                tenant_id=tenant,
                version=1,
                merchant_name="Smoke Cafe",
                currency="GBP",
                total=Decimal("4.50"),
                subtotal=Decimal("4.50"),
                items=[{"description": "Latte", "quantity": "1", "unit_price": "4.50", "amount": "4.50"}],
                discounts=[],
                warnings=[],
                provider="smoke",
                model="fixture",
            )
        )
        await db.commit()
        return doc.id


async def cleanup() -> None:
    async with get_sessionmaker()() as db:
        await db.execute(delete(Tenant).where(Tenant.id.in_(tenants)))
        await db.execute(delete(User).where(User.email.in_([EMAIL, VIEWER_EMAIL, OTHER_EMAIL])))
        await db.execute(
            delete(RateLimitBucket).where(
                RateLimitBucket.key.in_(
                    [f"login:email:{EMAIL}", f"login:email:{OTHER_EMAIL}", *(f"invite:tenant:{t}" for t in tenants)]
                )
            )
        )
        await db.commit()


def check(response: httpx.Response, status: int, label: str) -> httpx.Response:
    assert response.status_code == status, f"{label}: expected {status}, got {response.status_code}"
    print(f"{label}: {status}", flush=True)
    return response


async def main() -> None:
    try:
        doc_id = await seed()
        async with (
            httpx.AsyncClient(base_url=BASE, headers={"Origin": BASE}, timeout=30) as admin,
            httpx.AsyncClient(base_url=BASE, headers={"Origin": BASE}, timeout=30) as viewer,
            httpx.AsyncClient(base_url=BASE, headers={"Origin": BASE}, timeout=30) as other,
        ):
            check(await admin.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD}), 200, "Admin login")
            for path in ["/receipts", "/members", "/settings"]:
                response = check(await admin.get(path), 200, path)
                for link in ["/receipts", "/members", "/settings"]:
                    assert f'href="{link}"' in response.text
            check(
                await admin.post("/api/v1/invitations", json={"email": VIEWER_EMAIL, "role": "viewer"}),
                202,
                "Invite member",
            )
            pending = check(await admin.get("/api/v1/invitations"), 200, "Pending invitations").json()
            assert any(i["email"] == VIEWER_EMAIL for i in pending)
            token = None
            async with httpx.AsyncClient(timeout=10) as mail:
                for _attempt in range(30):
                    messages = (
                        (await mail.get("http://localhost:8025/api/v1/search", params={"query": f"to:{VIEWER_EMAIL}"}))
                        .json()
                        .get("messages", [])
                    )
                    if messages:
                        body = (await mail.get(f"http://localhost:8025/api/v1/message/{messages[0]['ID']}")).json()[
                            "Text"
                        ]
                        match = re.search("token=([\\w-]+)", body)
                        assert match
                        token = match[1]
                        break
                    await asyncio.sleep(0.5)
            assert token, "Invitation was not delivered to local Mailpit"
            print("Invitation email delivered", flush=True)
            check(
                await viewer.post("/api/auth/accept-invite", json={"token": token, "password": PASSWORD}),
                200,
                "Accept invitation",
            )
            me = check(await viewer.get("/api/v1/me"), 200, "Viewer session").json()
            viewer_id = me["user_id"]
            html = check(await viewer.get("/"), 200, "Viewer navigation").text
            assert 'href="/receipts"' in html and 'href="/members"' not in html and 'href="/settings"' in html
            for path in ["/api/v1/members", "/api/v1/api-keys", "/api/v1/audit-log"]:
                check(await viewer.get(path), 403, "Viewer denied " + path)
            detail = check(await viewer.get(f"/api/v1/receipts/{doc_id}"), 200, "Receipt detail").json()
            assert detail["total"] == "4.50" and detail["items"][0]["description"] == "Latte"
            check(await admin.patch(f"/api/v1/members/{viewer_id}", json={"role": "editor"}), 204, "Change member role")
            assert (await viewer.get("/api/v1/me")).json()["tenant"]["role"] == "editor"
            key = check(
                await admin.post("/api/v1/api-keys", json={"name": "Smoke integration", "role": "viewer"}),
                201,
                "Create API key",
            ).json()
            listed = check(await admin.get("/api/v1/api-keys"), 200, "List API keys").json()
            assert all("api_key" not in item and "key_hash" not in item for item in listed)
            check(
                await admin.get("http://localhost:8000/v1/documents", headers={"X-API-Key": key["api_key"]}),
                200,
                "Use API key",
            )
            check(await admin.delete(f"/api/v1/api-keys/{key['id']}"), 204, "Revoke API key")
            check(
                await admin.get("http://localhost:8000/v1/documents", headers={"X-API-Key": key["api_key"]}),
                401,
                "Revoked key denied",
            )
            events = check(await admin.get("/api/v1/audit-log"), 200, "Audit history").json()
            assert {"member.invited", "member.role_changed", "apikey.created", "apikey.revoked"} <= {
                e["action"] for e in events
            }
            check(
                await other.post("/api/auth/login", json={"email": OTHER_EMAIL, "password": PASSWORD}),
                200,
                "Other workspace login",
            )
            check(await other.get(f"/api/v1/receipts/{doc_id}"), 404, "Cross-workspace receipt denied")
            check(
                await admin.post(
                    "/api/v1/invitations",
                    json={"email": "unused@example.com", "role": "viewer"},
                    headers={"Origin": "http://evil.example"},
                ),
                403,
                "Cross-origin mutation denied",
            )
            check(await admin.delete(f"/api/v1/members/{viewer_id}"), 204, "Remove member")
            check(await viewer.get("/api/v1/me"), 401, "Removed member session denied")
            check(await admin.post("/api/auth/logout"), 204, "Admin logout")
            check(await other.post("/api/auth/logout"), 204, "Other workspace logout")
    finally:
        await cleanup()
        print("Temporary workspaces, accounts and seeded receipt removed", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
