"""Deterministic seed corpus for `evals/golden.jsonl`.

Usage:
    uv run python evals/seed_corpus.py --email eval@example.com --tenant eval
"""

import argparse
import asyncio
import hashlib
import uuid

from rag.adapters.storage import get_storage
from rag.adapters.vectorstore import get_vectorstore
from rag.auth.service import bootstrap_admin
from rag.core.config import get_settings
from rag.db.models import Document
from rag.db.session import get_sessionmaker
from rag.ingestion.pipeline import ingest_document, upload_key

CORPUS: list[tuple[str, str, str, str]] = [
    (
        "install-guide.pdf",
        "text/markdown",
        "document",
        (
            "# Installation Guide\n\n"
            "## Linux CLI Installation\n"
            "Download the release package and run the installer script with `sudo`. "
            "Verify the archive integrity first with `sha256sum -c rag-cli.tar.gz.sha256`.\n\n"
            "## System Requirements\n"
            "Worker and API nodes require Linux or macOS with Python 3.12+, at least 4 GB RAM, and 2 CPU cores. "
            "Local development uses ports 3000 (web), 8000 (API), 5432 (Postgres), 6333 (Qdrant), "
            "9000 (object storage), and 8025 (Mailpit). Local models are cached in the `/models` volume.\n\n"
            "## Zero-Downtime Upgrades\n"
            "Run `alembic upgrade head`, deploy the updated API and worker containers, and swap the Qdrant alias "
            "if the embedding model changed."
        ),
    ),
    (
        "configuration.md",
        "text/markdown",
        "document",
        (
            "# Configuration Reference\n\n"
            "- Default HTTP request timeout: 30 seconds.\n"
            "- `DATABASE_URL`: PostgreSQL connection string (`postgresql+asyncpg://...`).\n"
            "- `JWT_SECRET`: must be at least 32 random characters; weak values like `changeme` are rejected.\n"
            "- `CHUNK_TOKENS=500`, `CHUNK_OVERLAP=50`.\n"
            "- `MAX_UPLOAD_BYTES=26214400` (25 MB per file), `MAX_REQUEST_BODY_BYTES=31457280` (30 MB).\n"
            "- `EMBEDDING_PROVIDER`: `ollama` (`bge-m3`, dim 1024) or `openai` (`text-embedding-3-small`, dim 1536).\n"
            "- `WORKER_POLL_INTERVAL_S=1.0`, `JOB_MAX_ATTEMPTS=3`.\n"
            "- `RL_LOGIN_PER_EMAIL=5`, `RL_LOGIN_PER_IP=20`, `RL_LOGIN_WINDOW_S=900`.\n"
            "- `ACCESS_TOKEN_TTL_S=900` (15 minutes), `REFRESH_TOKEN_TTL_S=1209600` (14 days).\n"
            "- `METRICS_TOKEN`: Bearer token required for `/metrics` in production."
        ),
    ),
    (
        "security-policy.md",
        "text/markdown",
        "document",
        (
            "# Security Policy & Controls\n\n"
            "## Authentication & Passwords\n"
            "Passwords are hashed with Argon2id (64 MiB memory, 3 iterations, 4 lanes). Access tokens expire in "
            "15 minutes; rotating refresh tokens expire in 14 days. Replaying a rotated refresh token triggers "
            "reuse detection, revoking the entire token family and logging `auth.refresh_reuse_detected`.\n\n"
            "## Multi-Tenant Isolation & RBAC\n"
            "Every Qdrant query and upsert enforces a mandatory `tenant_id` filter inside `VectorStore`. "
            "Roles are `viewer` (`chat:use`, `document:read`), `editor` (adds `document:write`, `document:delete`), "
            "and `admin` (adds `member:manage`, `apikey:manage`, `audit:read`). Conversations are strictly "
            "owner-only (admins cannot read other users' chats). The last admin cannot be demoted or removed.\n\n"
            "## Input & Output Hardening\n"
            "Luhn-valid card numbers (PANs) are masked to their last 4 digits during OCR. Decompression bombs are "
            "blocked (200 MB DOCX uncompressed cap, 40M image pixels, 200 PDF pages, 20 OCR pages). "
            "Retrieved chunks are HTML-escaped and wrapped in `<doc>` tags. Browser sessions use AES-256-GCM "
            "`__Host-rag_session` cookies, strict `Origin` checks, and per-request CSP nonces."
        ),
    ),
    (
        "api-reference.md",
        "text/markdown",
        "document",
        (
            "# API Reference (v1)\n\n"
            "- `POST /v1/chat`: streams SSE events (`status`, `model`, `token`, `answer`, `citations`, `debug`, "
            "`done`, `error`). Hybrid retrieval prefetches dense + sparse BM25 (IDF), fuses with RRF, and reranks "
            "with `BAAI/bge-reranker-base`, rewriting the query at most once when scores fall below `GRADE_THRESHOLD`.\n"
            "- `POST /v1/documents`: multipart upload up to 25 MB; returns `202 Accepted` or `200 OK` (`job_id=null`) "
            "on idempotent re-upload.\n"
            "- `GET /v1/conversations`: cursor pagination via `before` (`updated_at`) and `before_id` (`id`).\n"
            "- `GET /v1/receipts`: paginated structured receipts.\n"
            "- Service-account API keys (`rk_<prefix>_<secret>` via `X-API-Key` or `Bearer`) work on document, "
            "receipt, and audit-log endpoints; human-only endpoints (`chat`, `conversations`, `members`, "
            "`invitations`, `api-keys`, `PATCH /v1/me`, `switch-tenant`, `password/change`) require a user session."
        ),
    ),
    (
        "operations-runbook.md",
        "text/markdown",
        "document",
        (
            "# Operations Runbook\n\n"
            "- `JobBacklogStale`: verify `rag-worker` is running, inspect logs, and check `jobs` table by status. "
            "Running jobs with `locked_at` older than 15 minutes are reclaimed automatically.\n"
            "- `LlmRouteExhausted`: every provider in `config/models.yaml` failed; check Ollama and API keys.\n"
            "- Zero-downtime reindex: run `rag reindex --version <n>` to build `chunks_v<n>` and flip `chunks_current`.\n"
            "- Backups: run `scripts/backup.py` for `pg_dump` and Qdrant collection snapshots."
        ),
    ),
    (
        "billing-faq.md",
        "text/markdown",
        "document",
        (
            "# Receipts & Workspace FAQ\n\n"
            "- Supported receipt formats: PDF, JPEG, PNG, and WebP. General documents also support DOCX, HTML, "
            "Markdown, CSV, and plain text.\n"
            "- Arithmetic cross-checks: if line items or subtotal + tax + tip disagree with the printed total by "
            "more than $0.02, the printed values are kept and a mismatch warning is attached.\n"
            "- Re-inviting an email supersedes earlier unused invite links so only the newest link stays valid."
        ),
    ),
    (
        "corner-cafe.pdf",
        "text/markdown",
        "receipt",
        (
            "# Receipt: Corner Cafe\n"
            "Merchant: Corner Cafe\n"
            "Purchased: 2026-10-12 08:45:00\n"
            "Payment: card (VISA ending 4242)\n"
            "Item count: 3\n\n"
            "| Item | Qty | Unit price | Amount |\n"
            "| --- | --- | --- | --- |\n"
            "| Oat Latte | 2 | 4.75 | 9.50 |\n"
            "| Almond Croissant | 1 | 4.25 | 4.25 |\n\n"
            "Subtotal: 13.75 USD\n"
            "Tax: 1.10 USD\n"
            "Total: 14.85 USD"
        ),
    ),
    (
        "cloud-hosting-inv.pdf",
        "text/markdown",
        "receipt",
        (
            "# Receipt: Cloud Hosting Inc.\n"
            "Merchant: Cloud Hosting Inc.\n"
            "Purchased: 2026-10-31\n"
            "Billing period: October 2026\n"
            "Payment: card (MASTERCARD ending 8899)\n\n"
            "| Item | Qty | Unit price | Amount |\n"
            "| --- | --- | --- | --- |\n"
            "| Dedicated Kubernetes Cluster | 1 | 250.00 | 250.00 |\n\n"
            "Discount: Annual commitment discount -20.00\n"
            "Subtotal: 230.00 USD\n"
            "Discounts: 20.00 USD\n"
            "Tax: 18.40 USD\n"
            "Total: 248.40 USD"
        ),
    ),
]


async def seed(email: str, tenant_name: str, password: str) -> uuid.UUID:
    await get_vectorstore().ensure_collection()
    await get_storage().ensure_bucket()
    async with get_sessionmaker()() as db:
        tenant_id = await bootstrap_admin(db, email=email, password=password, tenant_name=tenant_name)
    bucket = get_settings().s3_bucket
    for title, mime, kind, content in CORPUS:
        raw = content.encode("utf-8")
        doc_id = uuid.uuid5(tenant_id, title)
        async with get_sessionmaker()() as db:
            if await db.get(Document, doc_id) is not None:
                continue
            key = upload_key(tenant_id, doc_id)
            await get_storage().put(key, raw, mime)
            db.add(
                Document(
                    id=doc_id,
                    tenant_id=tenant_id,
                    title=title,
                    kind=kind,
                    version=1,
                    mime_type=mime,
                    size_bytes=len(raw),
                    content_hash=hashlib.sha256(raw).hexdigest(),
                    status="queued",
                    source_uri=f"s3://{bucket}/{key}",
                )
            )
            await db.commit()
            await ingest_document(db, {"document_id": str(doc_id), "version": 1})
    return tenant_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default="eval@example.com")
    parser.add_argument("--tenant", default="eval-corpus")
    parser.add_argument("--password", default="eval-corpus-passphrase-2026")
    args = parser.parse_args()
    tenant_id = asyncio.run(seed(args.email, args.tenant, args.password))
    print(tenant_id)


if __name__ == "__main__":
    main()
