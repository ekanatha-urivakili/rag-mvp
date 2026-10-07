# MVP Architecture: Agentic RAG System

## 0. Review Notes

Current implementation/security evidence: [principal review, 7 October 2026](PRINCIPAL_CODE_REVIEW.md). Performance targets below remain targets; the conditional eval CI job requires configured infrastructure and credentials.

### Revision 2 — architecture review

| # | Before | Now | Why |
| --- | --- | --- | --- |
| 1 | 5 LLM agents (Supervisor, Router, Retriever, Generator, Critic) | One LangGraph state machine; only 2–3 nodes call an LLM | The graph *is* the supervisor. The old design made 5–8 LLM calls per query → slow, costly, hard to debug. |
| 2 | Dense-only vector search | Hybrid (dense + BM25 sparse) + cross-encoder reranker | Biggest retrieval-quality win per unit of effort; cheaper than agent loops. |
| 3 | Unbounded Critic → Retriever/Generator loops | Max 1 query-rewrite retry; deterministic grading via reranker score | Bounded latency and cost; no infinite loops. |
| 4 | Vector DB is the only store | Postgres = source of truth (docs, chunks, jobs, chats); Qdrant = derived index | Re-embedding, deletes, audit, and recovery become possible without re-parsing files. |
| 5 | Ingestion as a script | Async, idempotent jobs (content hash + deterministic point IDs) | Re-uploads don't duplicate; failures retry; API stays responsive. |
| 6 | "Qdrant or Chroma", "LangGraph or AutoGen", "Streamlit or Next.js" | One choice each, behind thin interfaces | Decisions made; vendor swaps isolated to adapters. |
| 7 | No conversation handling | Follow-ups condensed into standalone queries; history in Postgres | Chat follow-ups ("what about the second one?") otherwise retrieve garbage. |
| 8 | Evaluation in week 6 | Golden dataset in week 1; eval runs in CI | Every chunking/prompt/model change is measured, not guessed. |
| 9 | Missing | Multi-tenancy, prompt-injection handling, tracing, retries/timeouts | Cheap to add now, expensive to retrofit. |

### Revision 3 — platform requirements

| # | Requirement | Design | Section |
| --- | --- | --- | --- |
| 10 | Podman for localhost | `compose.yaml` + `Containerfile`, run with `podman compose`; Ollama runs on the host for GPU access | §7.1 |
| 11 | Mailpit for email | Transactional email via SMTP through a jobs-table outbox; Mailpit locally, real SMTP provider in prod via env only | §4.8 |
| 12 | RBAC | Tenant-scoped roles (`admin`, `editor`, `viewer`), permission checks as FastAPI dependencies, audit log | §4.7 |
| 13 | Local Ollama + Anthropic + OpenAI | Per-node model routing with ordered fallbacks; local `qwen3.5:4b` for orchestration steps, Claude for generation, OpenAI as fallback and eval judge | §4.3 |

### Revision 4 — chat history and Next.js UI

| # | Requirement | Design | Section |
| --- | --- | --- | --- |
| 14 | Keep a history of chats and continue any of them | `conversations.updated_at` (last activity) drives ordering; keyset-paginated, searchable history; rename and delete; deep links `/c/{id}`; posting to an existing conversation continues it with its last turns as context | §4.10 |
| 15 | Next.js UI, built with the OWASP Web and API Top 10 in mind | Next.js App Router as a **backend-for-frontend (BFF)**: tokens only in an encrypted HttpOnly cookie, allowlisted server-side proxy to the API, Origin-checked mutations, per-request nonce CSP, no raw HTML rendering | §6 |

---

## 1. Scope

**Goal:** Email-verified signup creates a private workspace; invited users join an existing workspace. Users upload documents and ask questions in a chat UI; answers are grounded in those documents, streamed, and cite sources. When the documents don't contain the answer, the system says so. Access is controlled by role.

**MVP non-goals** (explicitly deferred): fine-tuning, GraphRAG, web search tools, per-document ACLs (tenant-level roles only), custom roles, SSO/OIDC, agents with side-effecting tools, multi-region. OCR of images/scanned PDFs and structured receipt extraction are implemented.

### Targets (MVP)

| Metric | Target |
| --- | --- |
| p95 time-to-first-token | < 3 s |
| Retrieval recall@8 on golden set | ≥ 0.85 |
| Faithfulness (golden set) | ≥ 0.90 |
| Ingestion | 100-page PDF searchable in < 2 min |
| LLM calls per query | ≤ 3 (typical: 2, of which 1 is local) |

---

## 2. Technology Stack

| Concern | Choice | Notes |
| --- | --- | --- |
| Language / tooling | Python 3.12, `uv`, `ruff`, `mypy`, `pytest` | |
| API | FastAPI + Pydantic v2 | SSE for streaming (simpler than WebSockets, works through proxies) |
| Orchestration | LangGraph | Graph only; LLM SDKs called directly via thin adapters (no LangChain chat-model layer) |
| LLM — orchestration steps | Ollama `qwen3.5:4b` (local, host) | Query analysis, rewrite, direct replies. Fallback: Claude Haiku 4.5 |
| LLM — generation | Anthropic Claude Sonnet 5.5 | Fallback: OpenAI (model set in config) |
| LLM — eval judge | OpenAI (model set in config) | Different vendor from the generator avoids self-preference bias |
| LLM SDKs | `anthropic`, `openai`, `ollama` | One adapter each behind an `LLM` protocol (§4.3) |
| Embeddings (dense) | `text-embedding-3-small` (1536-d) or an Ollama embedding model (e.g. `bge-m3`, 1024-d) | Fixed per collection; switching = re-index via alias (§4.5) |
| Sparse / keyword | BM25 via `fastembed` (CPU, local) | Stored as Qdrant sparse vectors |
| Reranker | `bge-reranker-v2-m3` (local cross-encoder) | Behind a `Reranker` protocol |
| Vector DB | Qdrant | Native hybrid search + RRF fusion, payload indexes, multi-tenancy, horizontal scaling |
| Relational DB | PostgreSQL 16 + SQLAlchemy 2 (async) + Alembic | Source of truth; job queue; LangGraph checkpointer |
| Job queue | Postgres `jobs` table + `SELECT … FOR UPDATE SKIP LOCKED` worker | Ingestion, email, re-index jobs. No extra infra for MVP |
| File storage | S3-compatible (MinIO locally) | Raw uploads, immutable |
| Parsing | `pymupdf4llm` (PDF → Markdown), `python-docx`, `trafilatura` (HTML) | Add Docling/Unstructured later for complex layouts |
| Auth | Email + password (`argon2id`), JWT access + rotating refresh tokens, API keys for service accounts | `pwdlib`/`argon2-cffi`, `pyjwt` |
| Authorization | RBAC, 3 fixed roles per tenant, permissions in code | §4.7 |
| Email | `aiosmtplib` + Jinja2 templates; **Mailpit** locally | Outbox via jobs table (§4.8) |
| Observability | OpenTelemetry + Langfuse, structured JSON logs | Langfuse is an optional compose profile locally (it's heavy) |
| Evaluation | Retrieval metrics + RAGAS (faithfulness, answer relevancy) | |
| UI | Next.js 16 (App Router) + React 19 + TypeScript, Tailwind CSS 4, TanStack Query | Runs as a BFF in front of the API (§6). Streamlit stays for Receipts/Members/Settings until they are ported, then is removed |
| Containers | **Podman** (rootless) + Compose spec (`compose.yaml`) | Same OCI images in prod |

---

## 3. High-Level Design (HLD)

Two independent paths share storage: **Ingestion (write path)** and **Query (read path)**. Every request passes **AuthN → RBAC** before reaching either.

```mermaid
graph LR
    User((Browser)) -->|same-origin only<br/>session cookie| Web[Next.js BFF<br/>UI + allowlisted proxy]
    Web -->|REST + SSE<br/>Bearer token| API[FastAPI<br/>AuthN + RBAC<br/>stateless, N replicas]

    subgraph Query Path
        API --> Graph[LangGraph RAG Graph]
        Graph --> Ret[Hybrid Retriever + Reranker]
        Graph --> MR[Model Router]
    end

    subgraph LLM Providers
        MR --> Ollama((Ollama<br/>local host))
        MR --> Anthropic((Anthropic API))
        MR --> OpenAI((OpenAI API))
    end

    subgraph Background Worker
        Worker[Worker<br/>N replicas] -->|claim job| PG
        Worker --> Obj
        Worker -->|upsert| VDB
        Worker -->|SMTP| Mail[Mailpit local /<br/>SMTP provider prod]
    end

    API -->|upload| Obj[(Object Storage<br/>S3/MinIO)]
    API -->|enqueue jobs| PG
    Ret --> VDB[(Qdrant<br/>dense + sparse)]
    Graph --> PG[(PostgreSQL<br/>users, roles, docs, chunks,<br/>jobs, chats, audit)]
```

**Layering inside the codebase** (dependencies point inward):

```text
api (routers, auth deps) ──> use cases (graph, ingestion, auth, email) ──> domain models
                                        │
                                        └──> adapters (LLM, Embedder, VectorStore, Reranker,
                                                       ObjectStorage, Mailer, DB repositories)
```

Only vendor-swap points get a `Protocol`: `LLM`, `Embedder`, `VectorStore`, `Reranker`, `Mailer`. Nothing else is abstracted.

---

## 4. Low-Level Design (LLD)

### 4.1 Ingestion Pipeline

```mermaid
sequenceDiagram
    participant U as User/UI
    participant A as API
    participant S as Object Storage
    participant P as Postgres
    participant W as Worker
    participant Q as Qdrant

    U->>A: POST /v1/documents (file)
    A->>A: authN, require(document:write), validate type/size, sha256
    alt hash already ingested for tenant
        A-->>U: 200 {document_id, status: "ready"} (no-op)
    else new or changed
        A->>S: put raw file
        A->>P: insert document(status=queued) + job(type=ingest_document)
        A-->>U: 202 {document_id, job_id}
    end
    W->>P: claim job (FOR UPDATE SKIP LOCKED)
    W->>S: get raw file
    W->>W: parse → markdown → chunk
    W->>P: insert chunks (text, metadata)
    W->>W: embed dense (batched) + sparse (BM25)
    W->>Q: delete points where doc_id = X (previous version)
    W->>Q: upsert points (deterministic IDs)
    W->>P: document.status = ready
```

#### Steps & rules

1. **Parse** to Markdown so headings survive.
2. **Chunk** — structure-aware: split on headings first, then recursively by tokens.
   - Start at **~500 tokens, 50 overlap**; tune using the eval set.
   - Prepend the heading path to each chunk's embedded text (`"Guide > Install > Linux\n\n<chunk>"`).
3. **Embed** in batches (e.g. 64) with retry/backoff on 429/5xx.
4. **Idempotency**
   - Document dedupe: `(tenant_id, content_hash)` unique.
   - Point ID: `uuid5(NAMESPACE, f"{document_id}:{version}:{chunk_index}")` — re-running a job overwrites, never duplicates.
   - Re-upload of a changed doc: bump `version`, delete old points by `doc_id`, upsert new.
5. **Failure handling** — `attempts` incremented with exponential backoff; after 3 → `status=failed`, error surfaced in UI, and a `send_email` job notifies the uploader.
6. **Delete** — removes Qdrant points by filter, then chunks, then marks document deleted (raw file purged asynchronously). Recorded in `audit_log`.

### 4.2 Query Graph (Agentic / Corrective RAG)

```mermaid
graph TD
    Start([question + history]) --> QA[analyze_query<br/>local Ollama, structured output]
    QA -->|needs_retrieval = false| Direct[respond_direct<br/>local Ollama]
    QA -->|needs_retrieval = true| R[retrieve<br/>hybrid + rerank, no LLM]
    R --> G{grade<br/>top rerank score ≥ threshold?}
    G -->|yes| Gen[generate<br/>Claude, streamed, cited]
    G -->|no, attempts < 1| RW[rewrite_query<br/>local Ollama]
    RW --> R
    G -->|no, attempts exhausted| NA[insufficient_context<br/>“I couldn't find this in your documents”]
    Gen --> V[validate_citations<br/>deterministic]
    V --> End([answer + citations])
    Direct --> End
    NA --> End
```

| Node | Model route | Responsibility |
| --- | --- | --- |
| `analyze_query` | `orchestration` | One call returns `{standalone_query, needs_retrieval, filters}`. Condenses follow-ups using last N turns. |
| `respond_direct` | `orchestration` | Greetings, "what can you do", polite out-of-scope refusal. No document content. |
| `retrieve` | — | Qdrant hybrid query (dense + sparse prefetch, RRF, top 40) → rerank → top 8. Tenant filter always applied. |
| `grade` | — | Threshold on reranker score (tuned on eval set). |
| `rewrite_query` | `orchestration` | Reformulates once (synonyms / HyDE-style). Bounded to 1 retry. |
| `generate` | `generation` | Answers only from numbered context blocks; cites `[1]`, `[2]`. Streams tokens. |
| `validate_citations` | — | Drops citation markers that don't map to a retrieved chunk; attaches source metadata. |
| `insufficient_context` | — | Fixed honest response + suggestion to upload relevant docs. |

#### Graph state

```python
class RAGState(TypedDict):
    tenant_id: str
    user_id: str
    conversation_id: str
    question: str
    history: list[ChatTurn]          # last N turns, loaded from Postgres
    standalone_query: str
    needs_retrieval: bool
    filters: dict[str, str]
    chunks: list[ScoredChunk]
    attempts: int
    answer: str
    citations: list[Citation]
    models_used: list[ModelUsage]    # provider, model, tokens, latency, fallback flag
```

**Why no LLM Critic in the hot path?** It doubles latency and cost. Faithfulness is measured offline (eval suite, OpenAI judge) and on a sampled % of production traffic asynchronously. If eval shows hallucination problems, add an inline groundedness check behind a feature flag.

**Extending to multi-agent later:** new capabilities (SQL agent, web search, summarization) are added as subgraphs selected by `analyze_query`'s output, each with its own model route. The state contract and API do not change.

### 4.3 LLM Provider Layer & Model Routing

Three providers, one interface, routing by **purpose** (not by node), with ordered fallbacks.

```python
class LLM(Protocol):
    provider: str
    model: str

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,   # structured output when set
        max_tokens: int,
        temperature: float = 0.0,
    ) -> Completion: ...                          # text | parsed, usage, latency

    def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[str]: ...
```

| Adapter | SDK | Structured output | Notes |
| --- | --- | --- | --- |
| `OllamaLLM` | `ollama` (native `/api/chat`) | `format=<JSON schema>` | `think=False` for Qwen3.x on orchestration calls (latency); `keep_alive` set so the model stays loaded |
| `AnthropicLLM` | `anthropic` | Structured outputs / tool schema | Prompt caching on static system prompt |
| `OpenAILLM` | `openai` | Structured outputs (`response_format` JSON schema) | |

Every structured response is **re-validated with Pydantic** regardless of provider — small local models occasionally emit invalid JSON.

**Routing config** (`config/models.yaml`, overridable by env):

```yaml
providers:
  ollama:    { base_url: "${OLLAMA_BASE_URL}" }        # http://host.containers.internal:11434 in containers
  anthropic: { api_key_env: ANTHROPIC_API_KEY }
  openai:    { api_key_env: OPENAI_API_KEY }

routes:  # first entry = primary, rest = fallbacks in order
  orchestration:
    - { provider: ollama,    model: "qwen3.5:4b",        timeout_s: 8 }
    - { provider: anthropic, model: "claude-haiku-4-5",  timeout_s: 8 }
  generation:
    - { provider: anthropic, model: "claude-sonnet-5-5", timeout_s: 60 }
    - { provider: openai,    model: "${OPENAI_GENERATION_MODEL}", timeout_s: 60 }
  eval_judge:
    - { provider: openai,    model: "${OPENAI_JUDGE_MODEL}", timeout_s: 60 }
```

**`ModelRouter.for_purpose("orchestration")`** returns an `LLM` that tries each entry in order and falls through on: timeout, connection error (e.g. Ollama not running), 429/5xx after retries, or a structured output that fails Pydantic validation twice. Fallbacks are logged and counted (`llm_fallback_total{purpose,from,to}`).

Rules:

- **Streaming fallback** happens only before the first token is sent; after that, errors surface as an SSE `error` event (no mixing two models in one answer).
- Prompts are provider-neutral (plain system + user messages; no vendor-specific tags required for correctness).
- Swapping a model is a config change, then an **eval run** — the eval suite can run the `generation` route against each provider and report the results side by side.
- Sensitive tenants can later be pinned to a local-only route (`generation: [ollama/...]`) — no code change.

**Why local Ollama for orchestration?** These calls are short, structured, and on the critical path before generation: a local 4B model on Apple Silicon is fast, free, and keeps raw user questions local. Cloud fallback covers quality failures and "Ollama is down".

### 4.4 Prompting rules (generation)

- Retrieved text is wrapped in delimited blocks (`<doc id="1" source="...">…</doc>`) and the system prompt states it is **data, not instructions** (prompt-injection mitigation).
- "Answer only from the documents. If they don't contain the answer, say so."
- Prompts live in versioned files under `graph/prompts/`; prompt version is logged on every trace.

### 4.5 Data Model

#### PostgreSQL (source of truth)

```text
-- Identity & access
tenants          (id, name, created_at)
users            (id, email UNIQUE, password_hash, is_active, last_login_at, created_at)
memberships      (user_id, tenant_id, role[admin|editor|viewer], created_at)
                  PK (user_id, tenant_id)
email_tokens     (id, type[invite|password_reset], email, tenant_id NULL, role NULL,
                  token_hash, expires_at, used_at, created_by, created_at)
refresh_tokens   (id, user_id, token_hash, expires_at, revoked_at, replaced_by, created_at)
api_keys         (id, tenant_id, name, role, key_prefix, key_hash, last_used_at,
                  revoked_at, created_by, created_at)
audit_log        (id, tenant_id, actor_user_id, action, target_type, target_id,
                  metadata JSONB, ip, created_at)

-- Content
documents        (id, tenant_id, uploaded_by, title, source_uri, mime_type, content_hash,
                  version, status[queued|processing|ready|failed|deleted],
                  error, created_at, updated_at)
                  UNIQUE (tenant_id, content_hash)
chunks           (id, document_id, version, chunk_index, text, heading_path,
                  page_start, page_end, token_count)

-- Background work
jobs             (id, type[ingest_document|send_email|reindex], payload JSONB,
                  status[queued|running|done|failed], attempts, run_after,
                  locked_at, error, created_at)

-- Chat
conversations    (id, tenant_id, user_id, title, created_at, updated_at)
                  INDEX (tenant_id, user_id, updated_at, id)      -- history list + keyset paging
messages         (id, conversation_id, role, content, citations JSONB, trace_id,
                  prompt_version, provider, model, fallback_used, tokens_in,
                  tokens_out, latency_ms, debug JSONB, created_at)
                  INDEX (conversation_id, created_at)             -- latest window + "load earlier"
feedback         (message_id, user_id, rating SMALLINT, comment, created_at)
```

All tokens (invite, reset, refresh, API key) are stored **hashed** (SHA-256 of a 32-byte random secret); the plaintext is shown/sent once.

#### Qdrant (derived index — fully rebuildable from `chunks`)

- Collection `chunks_v{n}` accessed via alias `chunks_current` → zero-downtime re-embedding (e.g. switching to an Ollama embedding model): build `chunks_v2`, flip alias, drop `v1`.
- Vectors: `dense` (size from config, cosine), `sparse` (BM25, IDF modifier).
- Payload indexes: `tenant_id` (keyword, `is_tenant=true`), `doc_id`, `category`.
- Payload:

```json
{
  "tenant_id": "t_123",
  "doc_id": "uuid",
  "doc_version": 2,
  "chunk_index": 14,
  "text": "chunk text…",
  "heading_path": "Guide > Install > Linux",
  "source": "install-guide.pdf",
  "page": 7,
  "category": "technical_spec",
  "embedding_model": "text-embedding-3-small"
}
```

Single collection with `tenant_id` partitioning; the filter is enforced inside the `VectorStore` adapter so no caller can forget it.

### 4.6 API Contract (v1)

| Method | Path | Permission | Description |
| --- | --- | --- | --- |
| `POST` | `/v1/auth/login` | public | Email + password → access token + refresh cookie/token |
| `POST` | `/v1/auth/refresh` | refresh token | Rotate refresh token, new access token |
| `POST` | `/v1/auth/logout` | authenticated | Revoke refresh token |
| `POST` | `/v1/auth/password/forgot` | public | Always `202` (no account enumeration); emails reset link if user exists |
| `POST` | `/v1/auth/password/reset` | reset token | Set new password; revokes all refresh tokens |
| `POST` | `/v1/invitations` | `member:manage` | `{email, role}` → emails invite link |
| `POST` | `/v1/invitations/accept` | invite token | Create user (or attach existing) + membership |
| `GET` | `/v1/me` | authenticated | User, current tenant, role, permissions (UI uses this to show/hide actions) |
| `GET` | `/v1/members` | `member:manage` | List members |
| `PATCH` | `/v1/members/{user_id}` | `member:manage` | Change role (cannot demote the last admin) |
| `DELETE` | `/v1/members/{user_id}` | `member:manage` | Remove membership |
| `GET/POST/DELETE` | `/v1/api-keys` | `apikey:manage` | Service-account keys with a role |
| `GET` | `/v1/audit-log` | `audit:read` | Paginated |
| `POST` | `/v1/documents` | `document:write` | Multipart upload → `202 {document_id, job_id}` |
| `GET` | `/v1/documents`, `/v1/documents/{id}` | `document:read` | List / status, error, chunk count |
| `DELETE` | `/v1/documents/{id}` | `document:delete` | Remove doc + vectors |
| `POST` | `/v1/chat` | `chat:use` | `{conversation_id?, message, filters?}` → **SSE stream**. With `conversation_id`, continues that conversation |
| `GET` | `/v1/conversations?limit&q&before&before_id` | `chat:use` (own only) | History, most recently active first; title search; keyset cursor `(updated_at, id)` |
| `GET` | `/v1/conversations/{id}?limit&before` | `chat:use` (own only) | Latest `limit` messages (≤ 500) oldest-first + `has_more`; `before` loads earlier ones |
| `PATCH` | `/v1/conversations/{id}` | `chat:use` (own only) | `{title}` rename |
| `DELETE` | `/v1/conversations/{id}` | `chat:use` (own only) | Hard delete; messages and feedback cascade |
| `POST` | `/v1/messages/{id}/feedback` | `chat:use` (own only) | `{rating, comment?}` |
| `GET` | `/healthz`, `/readyz` | public | Readiness checks Postgres + Qdrant; reports Ollama reachability (degraded, not failed) |

#### SSE events from `/v1/chat`

```text
event: status     data: {"step": "retrieving"}
event: token      data: {"text": "The install "}
event: citations  data: [{"n":1,"doc_id":"…","source":"install-guide.pdf","page":7,"snippet":"…"}]
event: done       data: {"message_id":"…","conversation_id":"…"}
event: error      data: {"code":"llm_unavailable","message":"…"}
```

`tenant_id` and `user_id` come from the token, never from the request body.

### 4.7 Authentication & RBAC

#### Roles and permissions (tenant-scoped)

| Permission | viewer | editor | admin |
| --- | :---: | :---: | :---: |
| `chat:use` (own conversations only) | ✓ | ✓ | ✓ |
| `document:read` (list docs, see sources) | ✓ | ✓ | ✓ |
| `document:write` (upload, re-index) | | ✓ | ✓ |
| `document:delete` | | ✓ | ✓ |
| `member:manage` (invite, change role, remove) | | | ✓ |
| `apikey:manage` | | | ✓ |
| `audit:read` | | | ✓ |

- Roles and their permission sets are defined **in code** (`Role` and `Permission` enums + a `ROLE_PERMISSIONS` map). No roles table, no policy engine — move to DB-defined roles only when customers need custom roles.
- A user can belong to several tenants with different roles. The access token carries `sub` (user) and `tid` (active tenant); switching tenant issues a new token.
- **The role is loaded from `memberships` on every request** (one indexed lookup), not trusted from the JWT — role changes and removals take effect immediately.
- Enforcement is a FastAPI dependency on each route:

```python
@router.post("/v1/documents", status_code=202)
async def upload(
    file: UploadFile,
    ctx: Annotated[RequestContext, Depends(require(Permission.DOCUMENT_WRITE))],
) -> UploadAccepted: ...
```

- `RequestContext(user_id, tenant_id, role)` is passed into use cases and repositories; every tenant-owned query filters by `ctx.tenant_id`. Conversations additionally filter by `ctx.user_id` — **admins cannot read other users' chats**.
- Guardrails: the last admin of a tenant cannot be demoted or removed; users cannot change their own role.
- **Audit log** records: login failures (rate-limited), invites, role changes, member removal, API key create/revoke, document delete.

#### Authentication

- Passwords: `argon2id`; min length 12; login rate-limited per IP + email.
- Access JWT: 15 min, HS256 (secret from env) — switch to RS256 when other services verify tokens.
- Refresh token: 14 days, opaque, rotated on every use, stored hashed; reuse of a rotated token revokes the whole chain.
- API keys: `rk_<prefix>_<secret>`, tenant-bound with a role; same `require(...)` path.
- **Bootstrap:** `rag admin create --email … --tenant …` CLI creates the first tenant + admin (no open sign-up).

### 4.8 Email (Mailpit locally)

| Email | Trigger | Link expiry |
| --- | --- | --- |
| Invitation | Admin invites a member | 72 h, single use |
| Password reset | `/v1/auth/password/forgot` | 30 min, single use |
| Ingestion failed | Job reaches terminal `failed` | — |

- **Outbox pattern:** the API inserts a `send_email` job **in the same transaction** as the business change (e.g. the `email_tokens` row). The worker sends it with retries. No lost emails on SMTP failures; no emails for rolled-back transactions; requests never block on SMTP.
- `Mailer` protocol with one `SmtpMailer` (`aiosmtplib`). Jinja2 templates under `email/templates/` (HTML + plain-text versions).
- Links are built from `PUBLIC_UI_URL`; the token travels in the URL **fragment** or POST body, never logged.
- Config (env only, no code change between environments):

```text
SMTP_HOST=mailpit   SMTP_PORT=1025   SMTP_TLS=false   SMTP_FROM="RAG <no-reply@localhost>"   # local
SMTP_HOST=<provider> SMTP_PORT=587   SMTP_TLS=true    SMTP_USER/SMTP_PASSWORD from secrets  # prod
```

- Mailpit UI at `http://localhost:8025` to view sent mail. Integration tests assert on emails via Mailpit's REST API (`/api/v1/messages`) — e.g. "invite → read link from Mailpit → accept → login".

### 4.9 Project Structure

```text
rag-mvp/
├── pyproject.toml
├── compose.yaml                # Podman: api, worker, web, ui (legacy), postgres, qdrant, objectstore, mailpit
├── Containerfile               # one image; api / worker / ui chosen by command
├── .env.example
├── config/models.yaml          # model routes + fallbacks
├── src/rag/
│   ├── api/                    # routers, schemas, SSE, error handlers
│   ├── auth/                   # passwords, jwt, refresh tokens, api keys, rbac (Role, Permission, require)
│   ├── core/                   # config (pydantic-settings), logging, errors, telemetry
│   ├── domain/                 # Document, Chunk, Citation, ChatTurn, RequestContext (pure Pydantic)
│   ├── ingestion/              # parsers, chunker, pipeline
│   ├── retrieval/              # hybrid search + rerank + grading threshold
│   ├── graph/                  # state, nodes, graph builder, prompts/
│   ├── email/                  # templates/, send_email job handler
│   ├── worker/                 # job loop, handlers registry (ingest_document, send_email, reindex)
│   ├── adapters/
│   │   ├── llm/                # base.py (LLM protocol), ollama.py, anthropic.py, openai.py, router.py
│   │   ├── embedder.py, vectorstore.py, reranker.py, storage.py, mailer.py
│   ├── db/                     # SQLAlchemy models, repositories, alembic migrations
│   └── cli.py                  # admin bootstrap, reindex
├── web/                        # Next.js UI + BFF (§6)
│   ├── Containerfile           # standalone build, non-root, read-only root FS
│   └── src/
│       ├── proxy.ts            # per-request CSP nonce, HSTS, token refresh, page auth redirect
│       ├── app/api/auth/[action]/route.ts   # login/signup/reset/invite/switch/logout → session cookie
│       ├── app/api/v1/[...path]/route.ts    # allowlisted proxy to the API (JSON, multipart, SSE)
│       ├── app/(auth)/…        # sign in, verify signup, reset password, accept invite
│       ├── app/(app)/…         # chat (/ and /c/[id]), documents
│       ├── components/         # chat thread, history sidebar, markdown, documents
│       └── lib/                # session (JWE cookie), security (CSRF, allowlist, IP), upstream, SSE parser
├── ui/                         # Streamlit (legacy: receipts, members, settings until ported)
├── evals/
│   ├── golden.jsonl            # {question, expected_doc_ids, reference_answer}
│   └── run_eval.py             # --route generation --provider anthropic|openai|ollama
└── tests/                      # unit, integration (testcontainers on Podman), RBAC matrix tests
```

### 4.10 Chat History & Continuation

Every chat is a `conversation` owned by one user in one tenant. The history list and "continue where I left off" are built on Postgres alone; the graph stays stateless.

```mermaid
sequenceDiagram
    participant B as Browser
    participant W as Next.js BFF
    participant A as API
    participant P as Postgres
    participant G as RAG graph

    B->>W: open /c/{id}
    W->>A: GET /v1/conversations/{id}?limit=200
    A->>P: own conversation? (tenant_id, user_id) → latest 200 messages
    A-->>B: messages (oldest first) + has_more
    B->>W: POST /api/v1/chat {conversation_id, message}
    W->>A: POST /v1/chat (Bearer, X-Forwarded-For)
    A->>P: own conversation? bump updated_at, load last N turns, insert user message (commit)
    A->>G: question + history
    G-->>B: SSE status / model / token / answer / citations
    A->>P: insert assistant message, bump updated_at
    A-->>B: SSE done {message_id, conversation_id}
```

| Concern | Decision |
| --- | --- |
| Ordering | `updated_at` = last message activity (user turn or assistant answer). Renaming does **not** reorder. Backfilled from `max(messages.created_at)` in migration `0004`. |
| Paging the list | Keyset cursor `(updated_at, id)` (`before`, `before_id`), never `OFFSET`: stable while chats are continued or deleted between page loads, O(log n) on the `(tenant_id, user_id, updated_at, id)` index. |
| Search | Case-insensitive title match (`ILIKE`, wildcards escaped), ≤ 100 chars. Full-text search over message bodies is deferred until users ask for it (needs a `tsvector` index on `messages`). |
| Opening a long chat | Latest `limit` messages (default 200, max 500) plus `has_more`; "Load earlier" pages backwards by `created_at`. Bounded response size (OWASP API4). |
| Continuing | `POST /v1/chat` with `conversation_id`. The graph receives the last `HISTORY_TURNS` messages; `analyze_query` condenses them into a standalone query, so follow-ups ("and the second one?") retrieve correctly no matter how old the chat is. |
| Context growth | Only a fixed window of turns is sent to models, so cost and latency don't grow with chat length. A rolling per-conversation summary (one `orchestration` call when the window slides) is the next step if evals show long-chat follow-ups degrading. |
| Rename / delete | `PATCH {title}`; `DELETE` is a hard delete with `ON DELETE CASCADE` to messages and feedback (chat content is the user's data, not an audit record). |
| Access | Every query filters by `tenant_id` **and** `user_id`; another user's or tenant's ID is a 404, identical to a missing one. Admins cannot read, rename, delete or continue other users' chats. Covered by the RBAC matrix and `tests/integration/test_conversations.py`. |
| Workspace switch | Conversations are tenant-scoped; switching workspace shows that workspace's history only. |
| Interrupted answers | The user turn is committed before streaming. If the stream fails or the user stops it, the assistant turn is missing and the next turn simply continues; history stays consistent. |

---

## 5. Cross-Cutting Concerns

### 5.1 Reliability

- **Timeouts** on every external call (per-route LLM timeouts in `models.yaml`; embeddings 10 s; Qdrant 2 s; SMTP 10 s).
- **Retries** with exponential backoff + jitter (`tenacity`) on 429/5xx/connection errors only; then **model fallback** (§4.3).
- **Bounded loops**: graph `recursion_limit` set; max 1 rewrite.
- **Graceful degradation**: Ollama down → cloud fallback; reranker failure → RRF results; sparse failure → dense only. All logged as warnings with metrics.
- **Idempotent jobs**: ingestion via deterministic IDs; emails via job ID as `Message-ID` dedupe key; terminal `failed` state after max attempts.
- **Stateless API**: all state in Postgres/Qdrant; LangGraph checkpointer in Postgres.
- **Rate limiting** per tenant on `/v1/chat` and uploads; per IP + email on login and password reset.

### 5.2 Security

- AuthN/RBAC as in §4.7; tenant isolation enforced in repositories and the `VectorStore` adapter.
- **RBAC matrix test**: a parametrized test hits every route with every role and asserts allowed/denied — new routes without a `require(...)` fail CI.
- Upload validation: allow-listed MIME types (sniffed, not trusted from header), size cap (e.g. 25 MB), filename sanitization.
- Prompt injection: retrieved content delimited and labelled as data; no side-effecting tools in MVP.
- Data egress: document chunks go to the `generation` provider (Anthropic/OpenAI); orchestration steps stay local by default. Documented per route so tenants can be pinned to local-only later.
- Secrets (provider API keys, JWT secret, SMTP creds) via env / secret manager; never returned to the UI; `.env` git-ignored.
- No passwords, tokens, prompts or document text in application logs (traces in Langfuse with access control).
- Browser-facing controls (BFF token custody, CSRF, CSP, XSS-safe rendering) are in §6.3.

### 5.3 Observability

- One trace per request across API → graph nodes → LLM/embedding/Qdrant calls.
- Log per message: provider, model, `fallback_used`, prompt version, tokens, cost (Ollama = 0), latency per node, top rerank score.
- Dashboards/alerts: p95 latency, error rate, **fallback rate per route** (rising = Ollama or a provider is unhealthy), `insufficient_context` rate, thumbs-down rate, job failures (ingest + email).

### 5.4 Evaluation

- **Golden set** (50–100 real questions) with expected source documents.
- **Retrieval metrics** (deterministic): recall@k, MRR — on every change to chunking, embeddings, or retrieval.
- **Generation metrics**: RAGAS faithfulness + answer relevancy, judged by the `eval_judge` route (OpenAI).
- **Provider comparison**: same golden set run through each `generation` candidate (Claude, OpenAI, a local Ollama model) → quality, latency, and cost table that drives the routing config.
- **Orchestration check**: accuracy of `analyze_query` (`needs_retrieval` + standalone query) for the local model vs. the fallback — confirms the local 4B model is good enough.
- Runs in CI on PRs touching `ingestion/`, `retrieval/`, `graph/`, `config/models.yaml`; fails on regression beyond tolerance.

---

## 6. UI (Next.js BFF)

The UI is a Next.js 16 App Router app (`web/`) that also acts as the **backend-for-frontend**. The browser only ever talks to its own origin; the BFF holds the API tokens and calls FastAPI server-to-server. **The API stays the enforcement point** for authN, RBAC, tenancy and rate limits; the BFF adds browser-facing defences and never widens what a user can do.

```mermaid
graph LR
    B((Browser)) -->|"pages, /api/auth/*, /api/v1/*<br/>cookie: __Host-rag_session (JWE)"| P[proxy.ts<br/>CSP nonce · HSTS<br/>token refresh · login redirect]
    P --> RSC[Server components<br/>GET /v1/me]
    P --> AUTH[/api/auth/action<br/>zod-validated → set/clear cookie/]
    P --> PX[/api/v1/...path<br/>allowlist · Origin check<br/>stream JSON, multipart, SSE/]
    RSC -->|Bearer| API[FastAPI]
    AUTH -->|tokens never leave the server| API
    PX -->|Bearer + X-Forwarded-For| API
```

### 6.1 Pages

The shell loads `/v1/me` server-side and shows/hides actions by permission (UX only).

| Route | Who | Contents |
| --- | --- | --- |
| `/login` | public | Tabs: sign in, sign up (email verification), reset password |
| `/verify_signup`, `/reset_password`, `/accept_invite` | public (link token) | Token read server-side from `?token=`, stripped from the address bar on load; set password / workspace |
| `/` | `chat:use` | New chat: history sidebar + empty thread |
| `/c/{id}` | `chat:use` (own only) | Past conversation: latest 200 messages, "Load earlier", composer continues it. Streamed answers, status/model line, `[n]` citations with **Sources**, 👍/👎 feedback, Stop button |
| `/documents` | `document:read` | Live status table (polls only while ingesting); upload/delete with `document:write`/`document:delete` |
| Receipts, Members, Settings | as before | **Still in Streamlit** (`:8501`) — next migration step, then Streamlit is removed |

History sidebar: "New chat", debounced title search, groups (Today / Yesterday / Previous 7 days / Older), keyset "Load more", inline rename, two-step delete. The first answer in a new chat moves the URL to `/c/{id}` without re-fetching.

### 6.2 Session and token handling

- **Login** (`POST /api/auth/login`): the BFF calls `/v1/auth/login`, then stores `{access, refresh, access_exp}` in one cookie encrypted with **JWE `dir` + A256GCM** (key = SHA-256 of `SESSION_SECRET`). Response body is `{ok: true}`. Browser JavaScript cannot read the tokens; XSS could still perform actions using the session.
- **Cookie**: `HttpOnly`, `SameSite=Strict`, `Path=/`, 14-day max-age; `Secure` + `__Host-` prefix whenever `WEB_ORIGIN` is https (prod requires it).
- **Refresh**: `proxy.ts` refreshes when the access token has < 60 s left, rewrites the cookie on both the forwarded request and the response, so server components and route handlers see the new token. Refresh tokens rotate and **reuse revokes the whole family** (§4.7), so concurrent requests holding the same expiring cookie share one in-flight refresh, and the result is reused for 60 s. That cache is per process: run web replicas with **sticky sessions** (or move the cache to Redis) before scaling out. API unreachable ≠ invalid: only a 401 from `/v1/auth/refresh` clears the cookie.
- **Logout / workspace switch**: revoke the refresh family at the API, then clear/replace the cookie. Switching workspace revokes the old workspace's session.

### 6.3 OWASP mapping (Web 2021 / API 2023)

| Risk | Control in `web/` |
| --- | --- |
| **A01 / API1, API5** Broken access control | No authorization logic in the UI; the API checks tenant, owner and role on every call. The BFF proxies only an explicit `(method, path)` allowlist with UUID-shaped IDs (`lib/security.ts`); admin routes are not reachable until their pages exist. Path segments are restricted to `[A-Za-z0-9-]` (no traversal / encoded slashes). |
| **A01** CSRF | `SameSite=Strict` cookie **and** every non-GET BFF request must carry `Origin == WEB_ORIGIN` (fallback `Sec-Fetch-Site: same-origin`); otherwise 403. GETs are side-effect free. |
| **A02** Cryptographic failures | Tokens only inside an AES-256-GCM JWE cookie; HSTS (2 years) and `upgrade-insecure-requests` when https; `Secure` cookies; prod refuses a non-https `WEB_ORIGIN`. |
| **A03** Injection / XSS | React escaping everywhere; ESLint bans `dangerouslySetInnerHTML`. Model output is rendered with `react-markdown` with **raw HTML skipped**, images disallowed, `javascript:` URLs stripped, links `rel="noopener noreferrer nofollow"`. Per-request **nonce CSP** with `strict-dynamic`, `object-src 'none'`, `base-uri 'none'`, `frame-ancestors 'none'`, `img-src 'self' data: blob:` (blocks prompt-injection exfiltration through remote images), `connect-src 'self'`. |
| **A04** Insecure design | BFF pattern instead of tokens in `localStorage`; strict zod schemas (`strictObject`, unknown keys rejected) on every auth input; validation errors never echo values. |
| **A05 / API8** Misconfiguration | `poweredByHeader: false`, no production source maps, `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` (link tokens never leak via Referer), COOP/CORP, restrictive `Permissions-Policy`. Container: non-root, read-only root FS, `cap_drop: ALL`, `no-new-privileges`, bound to `127.0.0.1`. Error pages show no details. |
| **A06** Vulnerable components | `package-lock.json`; `npm audit --omit=dev --audit-level=high` in CI. |
| **A07 / API2** Auth failures | Rotating refresh tokens with reuse detection preserved (single-flight refresh); logout revokes server-side; 401 anywhere sends the user to sign-in and drops cached data. |
| **A08** Integrity | Lockfile installs (`npm ci --ignore-scripts` in the image); no third-party scripts or CDNs. |
| **A09** Logging | BFF logs no bodies, tokens or cookies; the API's request ID is passed through (`X-Request-ID`). |
| **A10 / API7** SSRF | The upstream origin is fixed server config (`API_URL`); user input only selects an allowlisted path, never a host. |
| **API4** Resource consumption | Auth and proxied JSON bodies capped at 16 KB; uploads at 30 MB (actual stream bytes, including chunked bodies). Queries over 2 KB return 414. Upstream timeout 30 s (chat: 5 min); browser disconnects cancel upstream work. Forwarded IPs are ignored by default (`TRUSTED_PROXY_HOPS=0`); users share the BFF IP bucket until trusted ingress is configured. API refresh is limited to 120 attempts/IP/minute by default. |

### 6.4 Not exposed to end users

- Model/temperature selection (server config).
- Debug details (graph path, rewritten query, scores) stay in the dev-only API `debug` event and the legacy UI; the Next.js UI doesn't render them.

---

## 7. Deployment

### 7.1 Local (Podman)

**Prerequisites (macOS):**

```bash
podman machine init --cpus 6 --memory 12288 --disk-size 60   # once
podman machine start
ollama serve                                                  # on the host (Metal GPU); not containerized
ollama pull qwen3.5:4b                                        # already installed
```

**Run:**

```bash
cp .env.example .env              # add ANTHROPIC_API_KEY, OPENAI_API_KEY
podman compose up -d              # core services
podman compose exec api rag admin create --email you@example.com --tenant demo
```

`podman compose` delegates to the installed compose provider (`docker-compose` or `podman-compose`); `compose.yaml` uses only the Compose spec, so it is portable.

| Service | Image | Port(s) | Notes |
| --- | --- | --- | --- |
| `api` | app image | 8000 | `uvicorn rag.api.main:app` |
| `worker` | app image | — | `python -m rag.worker`; reranker model cached in a volume |
| `web` | `web/Containerfile` | 3000 | Next.js UI + BFF; only service the browser uses; reaches `api` over the private `bff` network (fixed IP `172.31.250.2`, the only address the API trusts for `X-Forwarded-For`) |
| `ui` | app image | 8501 | Legacy Streamlit (receipts, members, settings) until ported |
| `postgres` | `postgres:16` | 5432 | named volume, healthcheck |
| `qdrant` | `qdrant/qdrant` | 6333, 6334 | named volume |
| `objectstore` | `rustfs/rustfs` | 9000, 9001 | named volume, S3-compatible |
| `mailpit` | `axllent/mailpit` | 1025 (SMTP), 8025 (UI) | dev only |
| Ollama | **host process** | 11434 | containers use `OLLAMA_BASE_URL=http://host.containers.internal:11434` |

Podman notes:

- **Ollama on the host**, not in a container: the Podman VM on macOS has no Metal GPU access, so containerized Ollama would run on CPU only. If containers can't reach it, start Ollama with `OLLAMA_HOST=0.0.0.0`.
- Use **named volumes** (not bind mounts) for data services — avoids rootless UID/permission issues and slow file sharing on macOS. On Linux with SELinux, bind mounts need `:Z`.
- `depends_on: { condition: service_healthy }` starts dependencies in order. The local Compose API runs `alembic upgrade head` on every start; production should use a coordinated release migration step.
- Integration tests use running Compose services with a separate `rag_test` database, `rag-test` bucket and `chunks_test` alias/collections. They do not use Testcontainers. Mailpit recipients are unique per test tenant; existing captured email is preserved.

### 7.2 MVP production

Same OCI image (built with `podman build`) deployed to a PaaS, or a single VM running **Podman Quadlet** (systemd-managed containers). Managed Postgres; Qdrant Cloud or self-hosted with snapshots; S3; real SMTP provider instead of Mailpit (env change only). Ollama on a GPU host if orchestration stays local in prod — otherwise point the `orchestration` route at Claude Haiku via config. Migrations run as a release step.

The `web` image runs behind the HTTPS ingress; keep the API private (with separately authenticated service-account access if needed). Set `ENV=prod`, an HTTPS `WEB_ORIGIN`, and matching `PUBLIC_UI_URL`. Leave `TRUSTED_PROXY_HOPS=0` for direct access; set it to the number of trusted ingress hops that overwrite/append the actual peer IP, normally `1` for one ingress. Next.js preserves an existing `X-Forwarded-For` header and does not add a hop. Prevent direct public access to `web` when enabling forwarded identity. Set the API's `FORWARDED_ALLOW_IPS` to the web replicas' addresses. Legacy Streamlit also needs its own HTTPS/header policy; its untrusted output is rendered as plain text.

### 7.3 Scale path (only when a metric demands it)

| Component | MVP | Evolve when | To |
| --- | --- | --- | --- |
| API | 1–2 replicas | CPU/latency | More replicas behind LB (stateless) |
| Worker | 1 replica | Job backlog | More replicas (SKIP LOCKED already safe) |
| Queue | Postgres `jobs` | Sustained high job throughput | Redis (arq) or SQS |
| Vector DB | Single Qdrant node | > ~10M vectors or HA needed | Qdrant cluster (shards + replicas) |
| Local LLM | Ollama on one host | Concurrency (`OLLAMA_NUM_PARALLEL` saturated) | Multiple Ollama hosts behind LB, or vLLM |
| Reranker | Local CPU | Latency under load | GPU instance or hosted rerank API |
| Authz | 3 fixed roles | Custom roles / per-doc ACLs | DB-defined roles; doc ACL payload filter in Qdrant |
| Auth | Email + password | Enterprise customers | OIDC/SSO (keep RBAC unchanged) |
| Web BFF | 1 replica | Concurrency / HA | More replicas with sticky sessions (refresh single-flight is per process), or a shared refresh lock in Redis |
| Agents | Corrective RAG graph | New use cases | Additional LangGraph subgraphs (SQL, web, tools) |

---

## 8. Implementation Plan (7 weeks)

### Phase 0 — Foundation (Week 1, days 1–3)

- Repo skeleton, `pyproject`, ruff/mypy/pytest, CI.
- `compose.yaml` + `Containerfile` with Postgres, Qdrant, MinIO, Mailpit; `podman compose up` green.
- Config via `pydantic-settings`; structured logging; OpenTelemetry wiring.
- LLM adapters (Ollama, Anthropic, OpenAI) + `ModelRouter` with fallbacks; smoke test hits all three.
- Draft golden eval set (≥ 50 Q&A with expected docs).
- **Exit:** all services healthy under Podman; a structured-output call succeeds on each provider; fallback fires when Ollama is stopped.

### Phase 1 — Identity, RBAC, Email (Weeks 1–2)

- Tenants, users, memberships, tokens, API keys, audit log; Alembic migrations.
- Login / refresh / logout; `require(...)` dependency; RBAC matrix test.
- `jobs` table + worker loop; `send_email` handler; invite + password-reset flows via Mailpit.
- Admin bootstrap CLI.
- **Exit:** invite → email in Mailpit → accept → login works end to end in an integration test; every role/route combination covered by the matrix test.

### Phase 2 — Ingestion (Weeks 2–3)

- Document endpoints (permission-guarded); object storage.
- `ingest_document` handler: parse, chunk, dense + sparse embed, Qdrant upsert with deterministic IDs; failure email.
- **Exit:** 100 docs ingested; re-upload is a no-op; deletion removes vectors; recall@k measured on golden set.

### Phase 3 — Retrieval + Baseline RAG (Weeks 3–4)

- Hybrid search with RRF, reranker, tenant filter enforcement.
- Single-pass generate (Claude) with citations; `/v1/chat` SSE; citation validation.
- **Exit:** baseline eval numbers recorded (recall@8, faithfulness, p95 TTFT); Claude vs. OpenAI comparison run.

### Phase 4 — Agentic Graph + Conversations (Weeks 4–5)

- LangGraph nodes: `analyze_query`, `respond_direct`, `grade`, `rewrite_query`, `insufficient_context` on the local `orchestration` route.
- Conversations/messages persistence (own-only access), history condensation, Postgres checkpointer.
- Tune grading threshold; measure local-model `analyze_query` accuracy vs. Haiku.
- **Exit:** eval ≥ Phase 3 baseline; follow-ups resolve correctly; ≤ 3 LLM calls/query; fallback rate < 5% in the eval run.

### Phase 5 — UI (Week 6)

- Streamlit pages: login/reset/accept-invite, chat, documents, members, settings.
- Permission-aware rendering via `/v1/me`; debug toggle.
- **Exit:** end-to-end demo with admin, editor, and viewer accounts.

### Phase 5b — Chat history + Next.js UI (Revision 4)

- **Done in this step:** migration `0004` (`conversations.updated_at`, indexes); history list ordering, keyset paging, title search, rename, delete, bounded message window with "load earlier"; tests for ordering, paging, search, rename/delete cascade, continuation context, and owner-only access.
- **Done in this step:** `web/` BFF (session cookie, refresh single-flight, allowlisted proxy, CSRF, CSP nonce, security headers); auth flows; chat with history sidebar and continuation; documents. CI job: lint, typecheck, format, unit tests, build, `npm audit`.
- **Next:** port Receipts, Members (invites, roles), Settings (API keys, audit log) — extend the BFF allowlist per page; Playwright end-to-end tests (login → chat → reopen → continue; CSRF and CSP assertions); remove Streamlit and the `ui` service; rolling conversation summary if long-chat evals require it.
- **Exit:** every Streamlit page has a Next.js equivalent; e2e suite green in CI; Streamlit removed.

### Phase 6 — Hardening & Launch (Week 7)

- Rate limits (chat, upload, login, reset); upload limits.
- Eval in CI with regression gate; dashboards and alerts (incl. fallback rate).
- Load test (e.g. Locust) against §1 targets, including Ollama concurrency.
- Build images with Podman; deploy; backup/snapshot for Postgres and Qdrant verified by a restore test.
- **Exit:** §1 targets met; runbooks for re-indexing, restore, and provider outage.

---

## 9. Key Decisions (ADR summary)

| Decision | Chosen | Rejected | Reason |
| --- | --- | --- | --- |
| Orchestration | LangGraph state machine | AutoGen; free-form multi-agent chat | Deterministic, testable control flow; checkpointing; extend with subgraphs |
| Agent count | 2–3 LLM nodes | 5 LLM agents | Latency, cost, debuggability; quality comes from retrieval |
| LLM access | Own thin adapters + `ModelRouter` (3 small files) | LiteLLM; LangChain chat models | Only 3 providers; need provider-specific structured output, prompt caching, and explicit fallback semantics. Revisit LiteLLM if providers grow beyond ~4. |
| Orchestration model | Local Ollama `qwen3.5:4b`, cloud fallback | Cloud-only | Free, low-latency on Apple Silicon, keeps questions local; fallback covers outages and quality misses |
| Generation model | Claude Sonnet 5.5, OpenAI fallback | Single provider | Provider outage doesn't take the product down; choice re-validated by eval |
| Retrieval | Hybrid + rerank | Dense only | Exact terms (IDs, error codes, names) fail with dense-only |
| Vector DB | Qdrant | Chroma, pgvector | Hybrid + multi-tenancy + clustering built in (pgvector viable if corpus stays small) |
| Source of truth | Postgres | Vector DB payloads | Rebuildable index, re-embedding, deletes, audit |
| Authorization | 3 fixed roles, permissions in code, role read from DB per request | DB-defined roles; Casbin/OPA | Enough for MVP, trivially testable; immediate revocation |
| Auth | Email/password + JWT + rotating refresh | OIDC now | No external IdP needed for MVP; OIDC slots in later without touching RBAC |
| Email | SMTP via jobs-table outbox; Mailpit locally | Sending inline; vendor SDK | No lost/phantom emails; provider swap is env-only |
| Queue | Postgres SKIP LOCKED | Celery/Redis | No extra infra at MVP volume |
| Containers | Podman (rootless) + Compose spec | Docker Desktop | Daemonless/rootless, no licensing concerns; Compose spec keeps it portable; Quadlet for single-VM prod |
| Streaming | SSE | WebSockets | One-way stream is enough; simpler infra |
| UI | Next.js App Router as a BFF | Streamlit; SPA holding tokens in `localStorage` | Product-grade UI; tokens never reachable from JS; one same-origin surface for CSP/CSRF. Streamlit got the MVP out; the API-first design made the swap a UI-only change |
| Chat history paging | Keyset `(updated_at, id)` | `OFFSET` | Stable while chats move and are deleted; constant cost per page |
| Long-chat context | Fixed window of last N turns + query condensation | Whole transcript; rolling summary now | Bounded cost/latency; add a rolling summary only if evals show degradation |
