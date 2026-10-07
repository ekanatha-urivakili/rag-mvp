import asyncio
import os
import re
import subprocess
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
_env = dotenv_values(ROOT / ".env")
_base = os.environ.get("TEST_DATABASE_URL") or (_env.get("DATABASE_URL") or "")
if not _base:
    pytest.skip("Integration tests need DATABASE_URL (.env) and running compose services", allow_module_level=True)
TEST_DB_URL = re.sub(r"/[^/]+$", "/rag_test", _base)
os.environ["DATABASE_URL"] = TEST_DB_URL
for key in ("QDRANT_URL", "QDRANT_API_KEY", "S3_ENDPOINT_URL", "S3_ACCESS_KEY", "S3_SECRET_KEY", "SMTP_PORT"):
    if _env.get(key):
        os.environ.setdefault(key, _env[key] or "")
os.environ["QDRANT_ALIAS"] = "chunks_test"
os.environ["S3_BUCKET"] = "rag-test"
MAILPIT_API = f"http://localhost:{_env.get('MAILPIT_UI_PORT') or 8025}/api/v1"

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from rag.auth.rbac import Role  # noqa: E402
from rag.auth.service import bootstrap_admin  # noqa: E402
from rag.db.models import Membership, User  # noqa: E402
from rag.db.session import get_sessionmaker  # noqa: E402

PASSWORD = "correct-horse-battery-staple"


async def _create_db() -> None:
    admin = create_async_engine(re.sub(r"/rag_test$", "/postgres", TEST_DB_URL), isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        exists = (await conn.execute(text("SELECT 1 FROM pg_database WHERE datname = 'rag_test'"))).scalar()
        if not exists:
            await conn.execute(text("CREATE DATABASE rag_test"))
    await admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    asyncio.run(_create_db())
    subprocess.run(
        [str(ROOT / ".venv/bin/alembic"), "upgrade", "head"],
        cwd=ROOT,
        check=True,
        env={**os.environ, "DATABASE_URL": TEST_DB_URL},
    )


@pytest.fixture(autouse=True)
async def _clean() -> AsyncIterator[None]:
    async with get_sessionmaker()() as db:
        await db.execute(
            text(
                "TRUNCATE tenants, users, memberships, email_tokens, refresh_tokens, api_keys, audit_log, "
                "documents, chunks, jobs, conversations, messages, feedback, rate_limits CASCADE"
            )
        )
        await db.commit()
    yield


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    from rag.adapters.storage import get_storage
    from rag.adapters.vectorstore import get_vectorstore
    from rag.api.main import app

    # ASGITransport doesn't run the lifespan; do its setup here.
    await get_storage().ensure_bucket()
    await get_vectorstore().ensure_collection()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        yield c


@dataclass
class Account:
    email: str
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


async def login(client: httpx.AsyncClient, email: str, password: str = PASSWORD) -> str:
    r = await client.post("/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def make_tenant(client: httpx.AsyncClient, name: str) -> Account:
    email = f"admin-{uuid.uuid4().hex}@{name}.example.com"
    async with get_sessionmaker()() as db:
        tenant_id = await bootstrap_admin(db, email=email, password=PASSWORD, tenant_name=name)
    token = await login(client, email)
    me = (await client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})).json()
    return Account(email, tenant_id, uuid.UUID(me["user_id"]), token)


async def add_member(client: httpx.AsyncClient, tenant: Account, role: Role, name: str | None = None) -> Account:
    from rag.auth.passwords import hash_password

    email = f"{name or role.value}-{uuid.uuid4().hex[:6]}@example.com"
    async with get_sessionmaker()() as db:
        user = User(id=uuid.uuid4(), email=email, password_hash=hash_password(PASSWORD))
        db.add(user)
        await db.flush()
        db.add(Membership(user_id=user.id, tenant_id=tenant.tenant_id, role=role.value))
        await db.commit()
        user_id = user.id
    return Account(email, tenant.tenant_id, user_id, await login(client, email))


@pytest.fixture
async def tenant(client: httpx.AsyncClient) -> Account:
    return await make_tenant(client, "acme")


async def mailpit_messages(to: str) -> list[dict[str, object]]:
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{MAILPIT_API}/search", params={"query": f"to:{to}"})
        r.raise_for_status()
        return list(r.json()["messages"])


async def mailpit_body(message_id: str) -> str:
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{MAILPIT_API}/message/{message_id}")
        r.raise_for_status()
        return str(r.json()["Text"])
