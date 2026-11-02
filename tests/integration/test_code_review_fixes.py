"""Regressions for CODE_REVIEW.md: upload/S3 ordering, delete without Qdrant, refresh grace, per-token limits."""

import contextlib
import uuid

import httpx
import pytest
from sqlalchemy import func, select

from rag.adapters.storage import get_storage
from rag.adapters.vectorstore import get_vectorstore
from rag.core.config import get_settings
from rag.db.models import Document, Job
from rag.db.session import get_sessionmaker
from rag.ingestion.pipeline import object_key
from tests.integration.conftest import PASSWORD, Account


async def test_failed_s3_put_leaves_no_document_or_job(
    client: httpx.AsyncClient, tenant: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_: object) -> None:
        raise ConnectionError("object store down")

    monkeypatch.setattr(get_storage(), "put", boom)
    with contextlib.suppress(Exception):
        r = await client.post("/v1/documents", files={"file": ("a.txt", b"hello")}, headers=tenant.headers)
        assert r.status_code >= 500
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(func.count()).select_from(Document))).scalar_one() == 0
        assert (await db.execute(select(func.count()).select_from(Job))).scalar_one() == 0


async def test_each_version_gets_its_own_object(client: httpx.AsyncClient, tenant: Account) -> None:
    first = await client.post("/v1/documents", files={"file": ("a.txt", b"one")}, headers=tenant.headers)
    second = await client.post("/v1/documents", files={"file": ("a.txt", b"two")}, headers=tenant.headers)
    assert first.status_code == second.status_code == 202
    doc_id = uuid.UUID(second.json()["document_id"])
    assert doc_id == uuid.UUID(first.json()["document_id"])
    async with get_sessionmaker()() as db:
        doc = await db.get(Document, doc_id)
        assert doc is not None and doc.version == 2
        key = object_key(doc.source_uri)
    assert key.startswith(f"{tenant.tenant_id}/{doc_id}/")
    assert await get_storage().get(key) == b"two"


async def test_duplicate_upload_skips_object_store(
    client: httpx.AsyncClient, tenant: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        await client.post("/v1/documents", files={"file": ("a.txt", b"x")}, headers=tenant.headers)
    ).status_code == 202
    puts: list[str] = []
    real = get_storage().put

    async def spy(key: str, data: bytes, mime: str) -> None:
        puts.append(key)
        await real(key, data, mime)

    monkeypatch.setattr(get_storage(), "put", spy)
    again = await client.post("/v1/documents", files={"file": ("b.txt", b"x")}, headers=tenant.headers)
    assert again.status_code == 200 and puts == []


async def test_delete_succeeds_when_vector_store_is_down(
    client: httpx.AsyncClient, tenant: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    up = await client.post("/v1/documents", files={"file": ("a.txt", b"hello")}, headers=tenant.headers)
    doc_id = up.json()["document_id"]

    async def boom(*_: object, **__: object) -> None:
        raise ConnectionError("qdrant down")

    monkeypatch.setattr(get_vectorstore(), "delete_document", boom)
    assert (await client.delete(f"/v1/documents/{doc_id}", headers=tenant.headers)).status_code == 204
    async with get_sessionmaker()() as db:
        purge = (await db.execute(select(Job).where(Job.type == "purge_document"))).scalars().all()
        assert [j.payload["document_id"] for j in purge] == [doc_id]


async def _login(client: httpx.AsyncClient, tenant: Account) -> str:
    r = await client.post("/v1/auth/login", json={"email": tenant.email, "password": PASSWORD})
    return str(r.json()["refresh_token"])


async def _refresh(client: httpx.AsyncClient, token: str) -> httpx.Response:
    return await client.post("/v1/auth/refresh", json={"refresh_token": token})


async def test_concurrent_refresh_within_grace_keeps_both_tokens(
    client: httpx.AsyncClient, tenant: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "refresh_reuse_grace_s", 10.0)
    rt0 = await _login(client, tenant)
    first = await _refresh(client, rt0)
    replay = await _refresh(client, rt0)  # second BFF replica with the same cookie
    assert first.status_code == replay.status_code == 200
    # Whichever cookie the browser keeps must still work.
    assert (await _refresh(client, first.json()["refresh_token"])).status_code == 200
    assert (await _refresh(client, replay.json()["refresh_token"])).status_code == 200


async def test_replay_without_grace_revokes_family(client: httpx.AsyncClient, tenant: Account) -> None:
    rt0 = await _login(client, tenant)
    rt1 = (await _refresh(client, rt0)).json()["refresh_token"]
    assert (await _refresh(client, rt0)).status_code == 401
    assert (await _refresh(client, rt1)).status_code == 401


async def test_reset_is_limited_per_token(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "rl_reset_per_token", 2)
    body = {"token": "a" * 43, "new_password": "a-valid-new-passphrase-2026"}
    for _ in range(2):
        assert (await client.post("/v1/auth/password/reset", json=body)).status_code != 429
    assert (await client.post("/v1/auth/password/reset", json=body)).status_code == 429
