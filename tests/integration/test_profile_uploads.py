import io

import httpx
from PIL import Image

from rag.auth.rbac import Role
from tests.integration.conftest import PASSWORD, Account, add_member, login


async def test_profile_and_password_change(client: httpx.AsyncClient, tenant: Account) -> None:
    viewer = await add_member(client, tenant, Role.VIEWER)
    saved = await client.patch("/v1/me", headers=viewer.headers, json={"name": "  Alex  "})
    assert saved.status_code == 204
    assert (await client.get("/v1/me", headers=viewer.headers)).json()["name"] == "Alex"
    assert (await client.get("/v1/me", headers=tenant.headers)).json()["name"] is None
    assert (
        await client.patch("/v1/me", headers=viewer.headers, json={"name": "Alex", "email": "x@example.com"})
    ).status_code == 422
    refresh = (await client.post("/v1/auth/login", json={"email": viewer.email, "password": PASSWORD})).json()[
        "refresh_token"
    ]
    new_password = "updated-passphrase-with-space "
    wrong = await client.post(
        "/v1/auth/password/change",
        headers=viewer.headers,
        json={"current_password": "wrong", "new_password": new_password},
    )
    assert wrong.status_code == 400
    assert (await client.get("/v1/me", headers=viewer.headers)).status_code == 200
    weak = await client.post(
        "/v1/auth/password/change", headers=viewer.headers, json={"current_password": PASSWORD, "new_password": "short"}
    )
    assert weak.status_code == 400
    changed = await client.post(
        "/v1/auth/password/change",
        headers=viewer.headers,
        json={"current_password": PASSWORD, "new_password": new_password},
    )
    assert changed.status_code == 204
    assert (await client.get("/v1/me", headers=viewer.headers)).status_code == 401
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": refresh})).status_code == 401
    await login(client, viewer.email, new_password)
    assert (
        await client.post("/v1/auth/password/change", json={"current_password": PASSWORD, "new_password": new_password})
    ).status_code == 401


async def test_receipt_upload_is_separate_and_content_checked(client: httpx.AsyncClient, tenant: Account) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buffer, "PNG")
    data = buffer.getvalue()
    receipt = await client.post("/v1/receipts", headers=tenant.headers, files={"file": ("scan.png", data, "image/png")})
    assert receipt.status_code == 202, receipt.text
    repeated = await client.post(
        "/v1/receipts", headers=tenant.headers, files={"file": ("scan.png", data, "image/png")}
    )
    assert repeated.status_code == 200
    assert repeated.json()["document_id"] == receipt.json()["document_id"]
    assert (await client.get("/v1/documents", headers=tenant.headers)).json()["total"] == 0
    uploads = (await client.get("/v1/documents?kind=receipt", headers=tenant.headers)).json()
    assert uploads["total"] == 1
    assert uploads["items"][0]["status"] == "queued"
    invalid = await client.post(
        "/v1/receipts", headers=tenant.headers, files={"file": ("scan.png", b"plain text", "image/png")}
    )
    assert invalid.status_code == 415
    text = await client.post(
        "/v1/receipts", headers=tenant.headers, files={"file": ("notes.txt", b"plain text", "text/plain")}
    )
    assert text.status_code == 415
    viewer = await add_member(client, tenant, Role.VIEWER)
    assert (
        await client.post("/v1/receipts", headers=viewer.headers, files={"file": ("scan.png", data, "image/png")})
    ).status_code == 403
