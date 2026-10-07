# RAG MVP

Multi-tenant document Q&A with receipt extraction. Users upload documents and receipt photos, then ask questions in a chat UI. Answers are streamed, grounded in those documents, and cited. Access is controlled by tenant-scoped roles. This repo implements [`docs/rag_mvp_architecture.md`](docs/rag_mvp_architecture.md).

```
Browser ──same-origin──> Next.js BFF (encrypted session cookie, allowlisted proxy, CSP)
                               │ REST/SSE + Bearer
                               ▼
                         FastAPI (JWT/API key → RBAC) ──> LangGraph corrective-RAG graph
                                 │                               ├─ Ollama qwen3.5:4b (orchestration)
                                 │                               └─ Claude Sonnet 5.5 → OpenAI → Ollama (generation)
                                 ├─ Postgres (source of truth, jobs outbox, rate limits, audit, receipts)
                                 ├─ Qdrant (dense bge-m3 + BM25 sparse, RRF) + local cross-encoder rerank
                                 └─ S3 (RustFS locally)
Worker: ingest (parse / OCR / receipt extraction: qwen3-vl → Claude → OpenAI) · email (Mailpit) · reindex · purge
```

## Features

**Chat over your documents**
- Corrective RAG: query analysis, hybrid retrieval (dense + BM25, fused with RRF), cross-encoder reranking, relevance grading, and at most one query rewrite before answering.
- Streamed answers over SSE, with citations validated against the retrieved chunks. Uncited answers become an explicit "not in your documents" response.
- The UI shows the current step and the model in use, including fallbacks. Every answer records its provider, model, token usage and latency.
- Chat history: every chat is saved and listed by last activity, grouped by day, searchable by title, renameable and deletable. Open any past chat (`/c/{id}`) and keep asking; follow-ups use its earlier turns. Long chats load the latest 200 messages with "Load earlier".
- Per-message thumbs up/down feedback.
- Model routing with fallbacks (`config/models.yaml`): local Ollama first for orchestration, Claude → OpenAI → local for generation. Runs fully local with no cloud keys.

**Documents**
- Upload PDF, DOCX, HTML, Markdown, plain text, and JPEG/PNG/WebP images (25 MB max). The type is detected from content, never from the client header.
- Scanned PDFs and images are OCR'd (RapidOCR, CPU).
- Background ingestion with live progress, retries, versioning (re-uploading changed content creates version N+1), idempotent re-upload, and retry by re-uploading a failed file.
- Deletion removes chunks, vectors and raw files asynchronously.

**Receipts** ([details](#receipts))
- Attach a receipt photo in chat, or upload it on the Documents page. A vision model extracts merchant, date and time, line items, discounts, subtotal, tax, tip, total, payment method, and card brand with the last 4 digits.
- Arithmetic is cross-checked and mismatches are flagged, never "fixed". Full card numbers are masked.
- A Receipts page lists receipts and shows details. Receipts are searchable from chat.

**Accounts and workspaces**
- Self-serve signup with email verification creates a private workspace where you are admin ([details](#signup)). Set `SIGNUP_ENABLED=false` for invitation-only use.
- Email invitations into existing workspaces, password reset, and switching between workspaces.
- Roles: **viewer** (chat, read documents), **editor** (+ upload/delete documents), **admin** (+ members, API keys, audit log). Guards prevent removing or demoting the last admin.
- API keys for programmatic access, scoped to a tenant and role.
- An audit log of security-relevant events.

**Platform**
- Tenant isolation is enforced in Postgres and Qdrant. Another tenant's IDs return 404.
- Postgres outbox for jobs and email, rate limits shared across replicas, and structured JSON logs with secret redaction.
- Zero-downtime re-embedding (`rag reindex`) through a Qdrant alias swap.
- Retrieval and faithfulness evals (`evals/run_eval.py`).
- Next.js web UI that keeps API tokens out of the browser (backend-for-frontend), with CSRF, CSP and XSS controls ([details](#web-ui)).
- OWASP Web/API Top 10 controls ([mapping](#owasp-coverage)).

## Quickstart

```bash
./start.sh
```

`start.sh` does the following:

1. Checks that Podman is installed and running. On macOS it starts the Podman machine if it's stopped.
2. Creates `.env` from `.env.example` with generated secrets if it doesn't exist yet.
3. Checks that the ports are free, and warns if Ollama or its models are missing.
4. Starts Postgres, Qdrant, RustFS and Mailpit, and waits for them to be healthy.
5. Runs ruff, strict mypy, and the full unit + integration suite, then the web UI's lint, typecheck, format check and unit tests (if `npm` is installed).
6. Builds the images, starts the API, worker, web UI and legacy UI, waits for health, and opens the web UI in your browser.

| Command | What it does |
| --- | --- |
| `./start.sh` | Everything above |
| `./start.sh --skip-tests` | Start without running checks and tests |
| `./start.sh --no-build` | Reuse the existing `localhost/rag-mvp:latest` and `localhost/rag-web:latest` images |
| `./start.sh --no-browser` | Don't open the browser |
| `./start.sh test` | Prerequisite checks, infrastructure, and tests only |
| `./start.sh stop` | Stop the stack. Data volumes are kept |

Once it's running:

| URL | What |
| --- | --- |
| http://localhost:3000 | Web UI. Use **Sign up**, or create an admin (below) |
| http://localhost:8501 | Optional legacy Streamlit UI (`podman compose --profile legacy up -d ui`) |
| http://localhost:8000/docs | OpenAPI (disabled when `ENV=prod`) |
| http://localhost:8025 | Project Mailpit inbox (signup verification, invites and password resets). Override with `MAILPIT_UI_PORT` |

```bash
podman compose exec api rag admin create --email you@example.com --tenant demo   # prompts for the password
```

**Local models.** Chat, embeddings and receipt extraction default to Ollama on the host. Tests don't need it.

```bash
OLLAMA_HOST=0.0.0.0 ollama serve
ollama pull qwen3.5:4b && ollama pull bge-m3 && ollama pull qwen3-vl
```

Add `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY` to `.env` to enable the cloud routes.

**Ollama and containers.** Ollama runs on the host so it can use the Metal GPU. Containers reach it through `host.containers.internal`, which only works when Ollama listens on all interfaces. That also exposes Ollama, which has no authentication, to your LAN. Keep the macOS firewall on, or use a cloud `orchestration` route instead.

### Manual setup (Podman)

```bash
cp .env.example .env            # then fill JWT_SECRET, POSTGRES_PASSWORD, QDRANT_API_KEY, S3_SECRET_KEY (+ provider keys)
podman build -t localhost/rag-mvp:latest -f Containerfile .
podman compose up -d --no-build
```

### Running on the host (development)

```bash
uv sync --extra dev
podman compose up -d postgres qdrant objectstore mailpit
set -a; . ./.env; set +a
alembic upgrade head
uvicorn rag.api.main:app --reload --no-server-header &
python -m rag.worker &
(cd web && npm ci && SESSION_SECRET=$SESSION_SECRET API_URL=http://localhost:8000 npm run dev)   # http://localhost:3000
(cd ui && API_URL=http://localhost:8000 streamlit run app.py)                                     # legacy, :8501
```

## API

All routes are versioned under `/v1`. Each one requires a JWT (`Authorization: Bearer`) or an API key (`X-API-Key`), except the public auth routes and health checks.

| Area | Endpoints | Permission |
| --- | --- | --- |
| Auth | `POST /auth/signup`, `/auth/signup/verify`, `/auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/switch-tenant`, `/auth/password/forgot`, `/auth/password/reset` | public, except logout/switch |
| Profile | `GET /me` | any role |
| Chat | `POST /chat` (SSE: `status`, `model`, `token`, `answer`, `citations`, `done`; pass `conversation_id` to continue a chat), `POST /messages/{id}/feedback` | `chat:use` |
| Chat history | `GET /conversations?limit&q&before&before_id` (most recent first, keyset paging, title search), `GET /conversations/{id}?limit&before`, `PATCH`/`DELETE /conversations/{id}` | `chat:use`, own only |
| Documents | `POST /documents`, `GET /documents[/{id}]`, `DELETE /documents/{id}` | `document:read` / `write` / `delete` |
| Receipts | `GET /receipts`, `GET /receipts/{document_id}` | `document:read` |
| Members | `GET /members`, `PATCH`/`DELETE /members/{user_id}`, `POST`/`GET /invitations` | `member:manage` |
| API keys | `GET`/`POST /api-keys`, `DELETE /api-keys/{id}` | `apikey:manage` |
| Audit | `GET /audit-log` | `audit:read` |
| Health | `GET /healthz`, `GET /readyz` (unversioned) | public |

Clients must use the SSE `answer` event as the final answer text. Streamed `token` events are provisional.

## Tests and checks

```bash
./start.sh test          # starts the test infrastructure, then runs everything below
uv run ruff check src tests ui evals && uv run mypy src   # mypy runs in strict mode
uv run pytest            # unit tests, plus integration tests against the compose services (uses database rag_test)
(cd web && npm run lint && npm run typecheck && npm test && npm run build)
uv run python evals/run_eval.py --tenant-id <uuid> --provider anthropic --min-recall 0.85 --min-faithfulness 0.90
```

The integration suite covers these flows end to end, including real emails read back from Mailpit:

- signup → verification email → private workspace → login, plus disabled signup, expired/reused links and rate limits
- invite → email → accept → login
- password reset, with session revocation
- refresh-token rotation and reuse detection; logout and replay revoke access tokens immediately
- the RBAC matrix: every route × every role
- tenant and owner isolation (BOLA)
- chat history: activity ordering, keyset paging, search, rename/delete cascade, continuing a chat with its history, owner-only rename/delete/continue
- upload type spoofing, idempotent re-upload, versioning and failed-upload retry
- receipt upload → OCR → extraction → receipts API, including tenant isolation and delete cascade
- concurrency regressions: concurrent uploads, worker lease expiry, reindex recovery and stale-vector filtering

`tests/unit/test_route_coverage.py` fails CI if someone adds a route without an auth dependency.

## Signup

The sign-in page includes **New here? Sign up**. Enter your email, open the verification link
from Mailpit (or your configured SMTP provider), then choose a workspace name and password.
For local development, open **http://localhost:8025** to read the email. Mailpit captures
messages instead of delivering them to your normal inbox. Its messages persist in this
project’s `mailpit` Podman volume across container restarts.
Verification creates a new private workspace and makes you its admin. It never adds you to
an existing workspace; those still require invitations. Workspace names include a unique suffix.

Signup links expire after 30 minutes and are single-use. Accounts and workspaces are created
only after email verification. Existing-email requests receive the same 202 response without
changing the account. Passwords follow the existing 12–128 character policy and are hashed
with Argon2id. Signup initiation and verification are rate-limited.

Set `SIGNUP_ENABLED=false` for invitation-only operation. Apply `alembic upgrade head` when
running on the host; Compose applies migration `0002` automatically on API startup.

## Receipts

Attach a receipt photo (JPEG, PNG, WebP) or PDF in **Chat**, or upload it on **Documents**. The worker:

1. OCRs images and text-less PDF pages with RapidOCR (PaddleOCR PP-OCR models on ONNX Runtime, CPU, bundled in the wheel).
2. Sends the image plus the OCR text to the `receipt_extraction` route in `config/models.yaml`:
   `ollama/${OLLAMA_VISION_MODEL}` (default `qwen3-vl:latest`) → Claude Sonnet 5.5 → OpenAI `${OPENAI_VISION_MODEL}`.
   Cloud entries are skipped without a key; Ollama falls through when the model isn't pulled or Ollama is down.
3. Validates the structured output (merchant, date/time, line items, discounts, subtotal, tax, tip, total, item count,
   payment method, card brand and last 4) and cross-checks the arithmetic. Mismatches become `warnings`; numbers are
   never rewritten. Only the last 4 card digits are stored, and Luhn-valid full card numbers in OCR text are masked.
4. Stores a `receipts` row and indexes a Markdown summary plus the OCR text, so chat can answer questions about it.

`documents.progress` shows the live step and the model being tried; the chat and Documents page display it.
`GET /v1/receipts` and `GET /v1/receipts/{document_id}` need `document:read`. Locally `qwen3-vl` takes about
2–3 minutes per receipt on Apple silicon. If no model can answer, the OCR text is still indexed without structured fields.
The container image installs `libgl1` and `libglib2.0-0t64`, which OpenCV needs.

## Web UI

`web/` is a Next.js 16 app that serves the UI and acts as a backend-for-frontend (BFF). Design and full OWASP mapping: [architecture §6](docs/rag_mvp_architecture.md#6-ui-nextjs-bff).

- **API tokens are inaccessible to browser JavaScript.** Sign-in goes through `/api/auth/*`; the BFF keeps the access and refresh tokens in one AES-256-GCM encrypted, `HttpOnly`, `SameSite=Strict` cookie (`__Host-` + `Secure` over https). Refresh is single-flight within one process; multiple web replicas require session affinity or shared refresh coordination.
- **Allowlisted proxy.** `/api/v1/*` forwards only listed method/path pairs with UUID-shaped IDs and streams JSON, multipart uploads and SSE. Actual bytes are capped at 16 KB for JSON and 30 MB for uploads. Queries over 2 KB return 414.
- **Client identity.** `TRUSTED_PROXY_HOPS=0` ignores forwarded IPs by default: direct clients share the BFF's API IP bucket. Enable a positive hop count only behind a trusted ingress that overwrites/appends the actual peer IP and prevents direct access to the web service. A single such ingress normally uses `1`; Next.js does not append a hop to an existing header. Compose passes this setting to `web`. Never enable it for direct public access.
- **CSRF:** `SameSite=Strict` plus an exact `Origin` check on every mutating request.
- **XSS:** per-request nonce CSP with `strict-dynamic`, no remote images, markdown rendered without raw HTML, and `dangerouslySetInnerHTML` banned by lint.
- Env: `SESSION_SECRET` (generated by `start.sh`), `WEB_ORIGIN` (must be https in prod), `API_URL`, `TRUSTED_PROXY_HOPS`.

After login, administrators see **Chat, Documents, Receipts, Members, and Settings**. Viewers and editors see Chat, Documents, and Receipts. Receipts lists extracted line items, totals, payment details, and review warnings; upload receipt images/PDFs on Documents. Members supports email invitations, pending invitations, role changes, and confirmed removal. Settings supports API key creation (secret displayed once), confirmed revocation, and paginated audit history. The API scopes data to the current workspace and enforces permissions on every request.

Streamlit is now optional (`podman compose --profile legacy up -d ui`); the default launcher starts the Next.js workspace app.

To repeat the live local workspace smoke test after starting the stack:

```bash
uv run python scripts/smoke_workspace.py
```

It creates unique temporary workspaces, checks invitations through Mailpit, member roles/removal, key creation/revocation, receipt reads, audit records, permissions and CSRF, then removes its accounts/workspaces. The receipt fixture is seeded; `tests/integration/test_receipts.py` separately exercises upload and real OCR with model extraction faked. Run this against the local development stack with its matching host-side `DATABASE_URL`.
Legacy Streamlit renders untrusted chat, citations and receipt text as plain text, preventing Markdown image requests from leaking document content. Rich answer Markdown remains available in the Next.js UI.

## OWASP coverage

The table below retains Web 2021 identifiers. The current [principal review](docs/PRINCIPAL_CODE_REVIEW.md) maps all Web 2025 and API 2023 categories and records residual deployment risks.

| Risk (Web 2021 / API 2023) | Implementation |
| --- | --- |
| **API1 BOLA / A01** | `tenant_id` and `user_id` always come from the token, never the request body. Tenant-owned resource queries filter by the authenticated tenant. Retrieval also checks Postgres for ready documents at the exact indexed version, excluding partial, failed, deleted and superseded content. Conversations and feedback also filter by `user_id`, so admins can't read other users' chats. The Qdrant tenant filter is applied inside `VectorStore`. Another tenant's IDs return 404, the same as missing IDs. UUIDv4 IDs. |
| **API2 Broken auth / A07** | argon2id with transparent rehash. 12–128 character policy (rejects passwords containing the email local-part). JWT is HS256 with the algorithm pinned and `iss`/`aud`/`exp`/`nbf`/`iat`/`jti`/`sid` required plus a `typ` check. Access TTL is 15 min. Sub-second `iat` makes revocation via `tokens_valid_after` (password reset) exact. Refresh tokens are opaque and stored as SHA-256 hashes. They rotate on every use, and reusing a rotated token revokes the whole token family. JWT `sid` binds access tokens to that family; each request checks a live refresh record, so logout and replay detection also revoke access immediately. Password reset invalidates all outstanding reset links and serializes against login/refresh. Refresh cookie: `HttpOnly`, `SameSite=Strict`, path-scoped. Login failures look identical whether or not the account exists, including timing (a dummy hash is verified). |
| **API3 BOPLA** | Every input schema sets `extra="forbid"` (no mass assignment). Explicit response models, so hashes and secrets never serialize. Validation errors never echo submitted values. |
| **API4 Resource consumption** | Postgres fixed-window rate limits shared across replicas: login per IP and per email, signup per IP and per email, verification/reset/accept per IP, forgot-password per IP and per email, chat per tenant and per user, uploads per tenant. 25 MB upload cap (streamed and bounded) plus an actual-byte request-body cap, including chunked multipart requests without an honest Content-Length. Zip-bomb guard on DOCX. Pagination caps. 4k-character questions. LLM `max_tokens` and timeouts. Graph `recursion_limit`, with at most 1 rewrite. |
| **API5 BFLA** | `require(Permission)` dependency on every route. The role is read from the DB on every request, so a demotion or removal applies immediately. RBAC matrix test, route-coverage test, last-admin and self-change guardrails. |
| **API6 Sensitive flows** | Invite and reset are single-use, short-lived and hashed at rest. Forgot-password always returns 202 (no account enumeration). |
| **API7 SSRF / A10** | No user-supplied URLs are ever fetched. HTML is parsed from the uploaded bytes only. Email links are built only from `PUBLIC_UI_URL`. |
| **API8 Misconfig / A05** | API security headers (CSP `default-src 'none'`, `nosniff`, `DENY`, `no-referrer`, `no-store`, HSTS in prod). CORS allowlist. `TrustedHostMiddleware`. No server header. Docs and OpenAPI off in prod. Startup refuses a weak `JWT_SECRET`, and in prod rejects wildcard CORS/hosts, non-HTTPS UI/CORS URLs, missing Qdrant authentication and default S3 secrets. Containers run non-root with `cap_drop: ALL` and `no-new-privileges`, and ports bind to 127.0.0.1. |
| **API9 Inventory** | Versioned `/v1` and a single router registry. OpenAPI is hidden in prod. |
| **API10 Unsafe API consumption** | Every LLM structured output is re-validated with Pydantic. Fallbacks on timeout, error or refusal. |
| **A03 Injection** | SQLAlchemy bound parameters only. Prompt injection: retrieved text sits in `<doc>` blocks labelled as data, and `</doc>` breakouts are neutralized. No side-effecting tools. Citation indices are validated deterministically; the SSE `answer` event carries the canonical cleaned text, and the UI replaces provisional text with it. Uncited document answers become insufficient-context responses. Valid indices alone do not prove factual faithfulness. Jinja autoescapes HTML email. Email subject header-injection guard. Streamlit never uses `unsafe_allow_html`. |
| **A04 Insecure design** | MIME is sniffed from content rather than trusted from the header. Filenames are sanitized. Storage keys are never derived from filenames. |
| **A06 Vulnerable components** | `uv.lock`, plus `pip-audit --strict` in CI (clean as of this commit). Web: `package-lock.json` and `npm audit --omit=dev --audit-level=high` in CI. |
| **A08 Integrity** | Outbox pattern: emails and jobs commit atomically with the business change. Deterministic Qdrant point IDs. |
| **A09 Logging** | JSON logs with redaction of bearer tokens, JWTs, API keys and `password=`/`token=`. The access log omits query strings. Audit log: failed logins, refresh-token reuse, invites, joins, role changes, removals, API key create/revoke, document delete, password reset. Delivered email payloads are scrubbed from `jobs`. |

## Deviations from the architecture doc

- **Object storage: RustFS instead of MinIO.** RustFS is the local S3-compatible store. Any S3 endpoint works through the `S3_*` env vars.
- **Reranker: `BAAI/bge-reranker-base`** (via fastembed/ONNX, CPU) instead of `bge-reranker-v2-m3`. fastembed doesn't ship v2-m3, and the swap avoids pulling in PyTorch. It's configurable with `RERANKER_MODEL`. `GRADE_THRESHOLD` (default 0.0, in logit units) should be tuned on the eval set.
- **Generation route has a final local fallback (`ollama/qwen3.5:4b`).** This lets the stack run with no cloud keys. Remove it in `config/models.yaml` if that's not wanted. Sonnet 5.5 runs at `effort: low` for latency. Server-side refusal fallback (beta) is enabled on that entry; set `refusal_fallback: false` to disable it.
- **No LangGraph Postgres checkpointer.** Conversation state is reloaded from `messages` on every turn, which is all the graph needs today.
- **Email links use `?token=`, not a URL fragment.** The links point at the web UI, which reads the token server-side, strips it from the address bar on load, and sends `Referrer-Policy: no-referrer`. Tokens are single-use and short-lived, and the API only ever receives them in POST bodies.
- **Two extra endpoints:** `POST /v1/auth/switch-tenant` and `GET /v1/invitations` (the pending list in the Members page). Job types also include `purge_document`, which handles async raw-file and chunk cleanup after a delete.
- **Faithfulness** is scored by a small LLM-judge in `evals/run_eval.py` on the `eval_judge` route, not the RAGAS library.
- **Observability:** structured JSON logs with request and trace IDs, and per-message provider, model, fallback, token and latency data stored on `messages`. OpenTelemetry and Langfuse wiring are not done yet.

## Operations

- **Re-embed or switch embedding model:** set `EMBEDDING_*`, then run `rag reindex --version 2`. The worker builds `chunks_v2` from Postgres (no re-parsing), atomically flips the `chunks_current` alias, and retains the previous collection for in-flight reads and manual rollback. Reindex serializes against ingestion/upload/delete, rebuilds an incomplete target on retry, and reads Postgres in bounded batches. Stop query traffic during embedding model/dimension changes; the API and worker need the same new embedding settings. Remove retired collections after the rollback window.
- **Job retries:** 3 attempts with exponential backoff. Terminal ingestion failures mark the document `failed` and email the uploader. Re-uploading that file queues another attempt. Workers hold a dedicated job-row lock during processing, preventing lease expiry from reclaiming a live job. Both successful and terminally failed email jobs have their link payloads scrubbed.
- **Secrets:** `.env` is git-ignored and `chmod 600`. In prod, inject them from a secret manager. The `web` service receives only `API_URL`, `WEB_ORIGIN` and `SESSION_SECRET`; the legacy `ui` service only `API_URL`. Neither gets the backend env file or model volume. Provider keys and database/JWT/storage credentials stay in API/worker services.


## Review and validation

See [docs/PRINCIPAL_CODE_REVIEW.md](docs/PRINCIPAL_CODE_REVIEW.md) for the current review, fixed findings, verification evidence,
and deployment considerations. Security mappings describe implemented controls, not an OWASP
certification or a penetration-test result. The web UI sets its own CSP and security headers; the legacy Streamlit UI does not,
so configure HTTPS and appropriate headers/query-string log redaction for it at the production ingress.

The API waits for local model warmup before serving. Ollama orchestration has a 30-second
request timeout because ingestion can evict the chat model; warmup alone does not prevent that.
`/readyz` checks Postgres, Qdrant, object storage, and Ollama when local embeddings or
orchestration require it. This proves service reachability, not model output quality.

PDF ingestion rejects more than `MAX_PDF_PAGES` (default 200) and checks raster dimensions against `MAX_IMAGE_PIXELS` before allocation. Refresh attempts are limited by `RL_REFRESH_PER_IP` (default 120) per `RL_REFRESH_WINDOW_S` (default 60). Invitation attempts are limited by `RL_INVITE_PER_TENANT` (default 20) per `RL_INVITE_WINDOW_S` (default 3600). Admin lists accept `limit` (1–100) and `offset` (0–100000); Both UIs provide page selection. Cookie-based API refresh requires an Origin in `CORS_ORIGINS`; server clients send the refresh token explicitly. Worker details stay in internal logs/job records; user-visible ingestion errors are generic. S3 partial deletion errors trigger job retries.

Integration tests use `rag_test`, bucket `rag-test`, alias `chunks_test`, and physical collections
`chunks_test_vN`, separate from the normal `chunks_current` / `chunks_vN` index. Never point
the destructive integration suite at a production Postgres instance.

Access tokens issued before `sid` was added require refresh or a new sign-in. Existing refresh
tokens remain usable.
