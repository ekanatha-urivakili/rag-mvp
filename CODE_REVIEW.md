# Principal Engineering Code Review & Architecture Audit

- **Repository:** `rag-mvp` (Moontig Multi-Tenant Agentic RAG & Receipt Extraction Platform)
- **Reference Specification:** [`docs/ARCHITECTURE.md`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/docs/ARCHITECTURE.md) (Rev 2)
- **Review Scope:** End-to-end review across Architecture Compliance, Security, Correctness, Concurrency & Database Locking, Performance & Scalability, Observability, Frontend/BFF (`web/`), Legacy UI (`ui/`), and Testing/CI.

---

## 1. Executive Summary

The codebase is unusually well-structured and disciplined for an MVP/v1 platform:
- **Strengths:** Clean separation of concerns (`core/`, `auth/`, `adapters/`, `ingestion/`, `receipts/`, `retrieval/`, `graph/`, `worker/`, `email/`, `api/`); strong multi-tenant isolation enforced consistently across Postgres queries and Qdrant payload filters; well-designed transactional email outbox; solid token family rotation and reuse detection; thoughtful Next.js BFF cookie security (`AES-256-GCM`, `__Host-` prefix, Origin CSRF validation, nonce CSP); and strong unit/integration/E2E test foundations.
- **Key Risks Identified:** Despite strong happy-path engineering, deep inspection uncovered **4 High-Severity (`P1`)**, **7 Medium-Severity (`P2`)**, and **4 Low-Severity (`P3`)** issues across process boundaries, locking semantics, LLM adapter contracts, and multi-page document handling, along with **6 outstanding roadmap deliverables** from [`docs/ARCHITECTURE.md`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/docs/ARCHITECTURE.md).

### Findings Summary Matrix

| ID | Severity | Category | Component | Title |
| :--- | :---: | :--- | :--- | :--- |
| **CR-01** | **P1 (High)** | Observability / Reliability | `worker/loop.py`, `prometheus.yml`, `alerts.yml` | Worker Prometheus metrics are never exposed or scraped; `IngestionFailureSpike` and `OnlineFaithfulnessLow` alerts are dead in production |
| **CR-02** | **P1 (High)** | Correctness / Data Loss | `receipts/ocr.py`, `ingestion/parsers.py` | Multi-page PDF receipts only send Page 1 to the Vision LLM; scanned PDFs silently drop pages >20 and hybrid PDFs skip OCR on image pages |
| **CR-03** | **P1 (High)** | Concurrency / Availability | `api/routers/documents.py`, `adapters/storage.py` | Synchronous S3 upload runs inside an open DB transaction while holding `Tenant` row lock and `lock_index` advisory lock; leaks S3 objects on commit failure |
| **CR-04** | **P1 (High)** | Security / Availability | `web/src/lib/security.ts`, `auth/service.py` | Next.js BFF collapses all browser clients into one container IP when `TRUSTED_PROXY_HOPS=0`, enabling global tenant/app lockout in 5–20 requests; missing per-account rate limit on password reset |
| **CR-05** | **P2 (Medium)** | Correctness / LLM Contract | `adapters/llm/anthropic.py`, `openai.py`, `ollama.py` | `AnthropicLLM.complete` and `OpenAILLM.complete` silently ignore the `temperature` parameter; `OllamaLLM.stream` hardcodes `temperature=0.2` |
| **CR-06** | **P2 (Medium)** | Reliability / Streaming | `adapters/llm/router.py`, `api/routers/chat.py` | `FallbackLLM.stream` applies a single total `timeout_s` deadline to the entire stream after the first token and discards partial answers on timeout |
| **CR-07** | **P2 (Medium)** | Correctness / Concurrency | `api/routers/chat.py`, `graph/graph.py` | User message is committed before graph execution; failed/cancelled streams leave consecutive `role="user"` messages in history, breaking strict alternating-role LLM APIs |
| **CR-08** | **P2 (Medium)** | Concurrency / Availability | `ingestion/reindex.py`, `ingestion/locking.py` | `reindex` holds an exclusive Postgres transaction-level advisory lock across the entire corpus re-embedding loop, blocking all uploads and deletes |
| **CR-09** | **P2 (Medium)** | Reliability / Consistency | `api/routers/documents.py`, `ingestion/pipeline.py` | `delete_document` calls Qdrant synchronously while holding DB locks, duplicating the async `purge_document` job and failing HTTP DELETE when Qdrant hiccups |
| **CR-10** | **P2 (Medium)** | Architecture / Spec Drift | `graph/graph.py`, `adapters/vectorstore.py`, `ingestion/pipeline.py` | `analyze_query` omits `filters` extraction (`§4.2`) and Qdrant omits the `category` payload field and index (`§4.5`) |
| **CR-11** | **P2 (Medium)** | Architecture / RBAC Contract | `api/routers/members.py`, `apikeys.py`, `auth/service.py` | Service-account API keys with `Role.ADMIN` are blocked from member and API-key management (`user_only=True`), contradicting `§4.6` |
| **CR-12** | **P3 (Low)** | Frontend / UX | `web/src/components/documents-view.tsx` | `DocumentsView` and `ReceiptUploads` hardcode `limit=100` with no pagination UI |
| **CR-13** | **P3 (Low)** | Scalability / BFF | `web/src/lib/upstream.ts` | In-memory refresh token single-flight map (`inflight` / `recent`) is process-local and breaks under multi-replica `web` deployments without sticky sessions |
| **CR-14** | **P3 (Low)** | Security / Defense-in-Depth | `auth/tokens.py`, `auth/service.py` | JWT validation omits `iss`/`aud` claims; `accept_invitation` for an existing logged-out user auto-joins the workspace via invite token without re-authenticating |
| **CR-15** | **P3 (Low)** | Testing / CI | `tests/integration/test_rbac_matrix.py`, `.github/workflows/ci.yml` | RBAC matrix test omits 6 mutating endpoints; CI `eval` regression gate is opt-in and only has 3 golden examples |

---

## 2. Architecture & Feature Implementation Matrix vs. `docs/ARCHITECTURE.md`

| Architecture Section / Phase | Requirement Summary | Status | Implementation Evidence & Notes |
| :--- | :--- | :---: | :--- |
| **§3 / §4.5 Vector Store** | Single Qdrant collection `chunks_v{n}` with alias `chunks_current`, dense + sparse (`BM25`) vectors, RRF fusion, payload indexes (`tenant_id`, `doc_id`, `category`) | ⚠️ **Partial** | Dense + BM25 + RRF + alias swap implemented in [`vectorstore.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/adapters/vectorstore.py#L46-L119). **`category` payload field & index are missing** (**CR-10**). |
| **§4.1 LLM Router & Model Config** | Config-driven `FallbackLLM` (`orchestration`, `generation`, `receipt_extraction`, `eval_judge`) with structured output validation & streaming fallback | ⚠️ **Partial** | Implemented in [`router.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/adapters/llm/router.py#L54-L163) and [`config/models.yaml`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/config/models.yaml). `temperature` dropped in Anthropic/OpenAI `complete()` (**CR-05**); whole-stream timeout bug in `stream()` (**CR-06**). |
| **§4.2 LangGraph Agentic RAG** | 5-node bounded state graph (`analyze_query`, `retrieve`, `generate`, `rewrite_query`, `respond_direct`), max 1 rewrite loop (`max_attempts=2`), structured inline citations | ⚠️ **Partial** | Implemented in [`graph.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/graph/graph.py#L71-L263). `analyze_query` only returns `{standalone_query, needs_retrieval}` and omits `filters` (**CR-10**). |
| **§4.3 Ingestion Pipeline** | Upload → magic-byte sniff → S3 → DB `queued` → worker parse (PyMuPDF/PaddleOCR/DOCX/HTML/CSV/MD) → structure-aware chunking → embed → deterministic UUIDv5 Qdrant upsert | ⚠️ **Partial** | Implemented in [`pipeline.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/ingestion/pipeline.py#L61-L189), [`parsers.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/ingestion/parsers.py), [`chunker.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/ingestion/chunker.py). Upload holds DB locks across S3 network I/O (**CR-03**); scanned PDFs silently truncate at 20 pages (**CR-02**). |
| **§4.4 Receipt Extraction** | Orientation normalize → PaddleOCR + Vision LLM multimodal extraction → arithmetic cross-check (`subtotal + tax - discount == total ± 0.02`) → `receipts` + searchable `chunks` | ⚠️ **Partial** | Implemented in [`extract.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/receipts/extract.py#L65-L212) and [`ocr.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/receipts/ocr.py#L84-L108). Multi-page PDF receipts only send Page 1 image to the Vision LLM (**CR-02**). |
| **§4.6 – §4.9 Auth, RBAC, Lifecycle, Email Outbox, Audit** | Multi-tenant workspaces, Argon2id (`64 MiB, t=3, p=4`), JWT (`15m`) + rotating refresh token families (`30d`), API keys, email verification, invitations, password reset, transactional `email_outbox`, append-only `audit_log` | ✅ **Implemented** | Implemented across [`src/rag/auth/`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/auth/service.py), [`src/rag/email/`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/email/outbox.py), and [`src/rag/core/audit.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/core/audit.py). See **CR-04**, **CR-11**, **CR-14** for rate-limiting and API-key RBAC findings. |
| **§4.10 Conversation Persistence** | `conversations` + `messages` tables, cursor-paginated history, sliding window (`last 4 turns`) fed to `analyze_query` | ✅ **Implemented** | Implemented in [`chat.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/chat.py#L50-L282). Rolling summary for long chats was explicitly deferred in `§4.10`. See **CR-07** for aborted-turn history bug. |
| **§5.1 – §5.2 Security & Quotas** | Per-tenant storage quota (`10 GiB`), atomic `SELECT ... FOR UPDATE`, sliding-window rate limits, magic-byte validation, prompt-injection delimiters | ✅ **Implemented** | Implemented in [`documents.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/documents.py#L70-L95), [`ratelimit.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/core/ratelimit.py), [`sniff.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/ingestion/sniff.py). |
| **§5.3 Observability** | OpenTelemetry traces (`rag-api`, `rag-worker`), Prometheus `/metrics`, Grafana dashboard, 5 alert rules, runbooks, online faithfulness sampling | ⚠️ **Partial** | Implemented in [`telemetry.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/core/telemetry.py), [`metrics.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/core/metrics.py), [`ops/`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/ops/prometheus/alerts.yml), [`docs/RUNBOOKS.md`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/docs/RUNBOOKS.md). **Worker metrics are never exposed/scraped** (**CR-01**). |
| **§5.4 / §8 Phase 0 & 6 Evaluation & Load Testing** | Golden dataset (`50–100` Q&A), `recall@8 ≥ 0.85`, `faithfulness ≥ 0.90`, `p95 TTFT < 3s`, `100-page PDF < 2 min`, CI regression gate, Locust load test | ❌ **Incomplete** | Harness ([`evals/run_eval.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/evals/run_eval.py)) and Locust ([`loadtest/locustfile.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/loadtest/locustfile.py)) exist, but [`evals/golden.jsonl`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/evals/golden.jsonl) only has **3 items** (vs. 50–100) and production latency/quality targets are not yet met. |
| **§6 Web UI & Next.js BFF** | Next.js 16 App Router, BFF session proxy, SSE streaming, citations drawer, receipts table + CSV export, members/API keys/audit/usage views, Playwright E2E | ✅ **Implemented** | Implemented in [`web/`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/web/src/app/api/v1/%5B...path%5D/route.ts). Legacy Streamlit UI ([`ui/`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/ui/app.py)) retirement remains outstanding (`§8 Phase 5b`). |

---

## 3. Detailed Code Review Findings

### P1 — High Severity Findings

---

#### CR-01: Worker Prometheus Metrics Are Never Exposed or Scraped — Production Alerts `IngestionFailureSpike` and `OnlineFaithfulnessLow` Are Dead
- **Severity:** `P1 (High)`
- **Category:** Observability / Production Readiness
- **Locations:**
  - [`src/rag/core/metrics.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/core/metrics.py#L28-L55)
  - [`src/rag/worker/loop.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/worker/loop.py#L140-L157)
  - [`ops/prometheus/prometheus.yml`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/ops/prometheus/prometheus.yml#L9-L13)
  - [`ops/prometheus/alerts.yml`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/ops/prometheus/alerts.yml#L41-L65)
- **Analysis:**
  - `rag-api` and `rag-worker` run in separate OS processes / containers ([`compose.yaml`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/compose.yaml#L83-L141)), each with its own in-process `prometheus_client` registry.
  - The following metrics are recorded **exclusively inside `rag-worker`** jobs:
    - `INGESTION_DOCS` (`rag_ingestion_documents_total`) and `INGESTION_DURATION` (`rag_ingestion_duration_seconds`) in [`pipeline.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/ingestion/pipeline.py#L149-L176) and [`extract.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/receipts/extract.py#L194-L209).
    - `ONLINE_FAITHFULNESS` (`rag_online_eval_faithfulness`) in [`judge.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/evaluation/judge.py#L72).
    - `LLM_CALLS` (`rag_llm_calls_total`) for `route="receipt_extraction"` and `route="eval_judge"`.
  - However, [`run_forever()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/worker/loop.py#L140-L157) in `rag-worker` never starts a Prometheus HTTP server (e.g., `prometheus_client.start_http_server`), and [`ops/prometheus/prometheus.yml`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/ops/prometheus/prometheus.yml#L9-L13) only scrapes `api:8000`.
- **Impact:**
  - In `api:8000/metrics`, `rag_ingestion_documents_total`, `rag_ingestion_duration_seconds`, and `rag_online_eval_faithfulness` have **zero observations forever**.
  - Two of the five production alerts in [`ops/prometheus/alerts.yml`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/ops/prometheus/alerts.yml#L41-L65) (`IngestionFailureSpike` and `OnlineFaithfulnessLow`) can **never fire**, and the corresponding panels in [`ops/grafana/dashboards/rag-overview.json`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/ops/grafana/dashboards/rag-overview.json) remain blank.
- **Remediation:**
  1. Start an internal metrics HTTP listener in [`run_forever()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/worker/loop.py#L140) (e.g., `start_http_server(settings.worker_metrics_port)` on port `8001`).
  2. Add `worker:8001` as a scrape target in [`ops/prometheus/prometheus.yml`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/ops/prometheus/prometheus.yml#L9-L13).

---

#### CR-02: Multi-Page PDF Receipts Only Send Page 1 to the Vision LLM; Scanned PDFs Silently Drop Pages >20 & Hybrid PDFs Skip OCR
- **Severity:** `P1 (High)`
- **Category:** Correctness / Silent Data Loss
- **Locations:**
  - [`src/rag/receipts/ocr.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/receipts/ocr.py#L101-L108) (`llm_image`)
  - [`src/rag/ingestion/parsers.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/ingestion/parsers.py#L19-L36) (`_pdf`, `_scanned_pdf`)
- **Analysis:**
  1. **Multi-page PDF receipts (`llm_image`):**
     - In [`ocr.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/receipts/ocr.py#L84-L98), `extract_ocr()` runs PaddleOCR over up to `max_ocr_pages` pages of a PDF receipt.
     - Immediately below, [`llm_image()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/receipts/ocr.py#L101-L108) opens the PDF and renders **only `doc[0]` (page 1)**:
       ```python
       if mime == "application/pdf":
           with pymupdf.open(stream=data, filetype="pdf") as doc:
               return for_llm(render_pdf_page(doc[0]))
       ```
     - The prompt in [`extract_receipt.v1.txt`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/receipts/prompts/extract_receipt.v1.txt#L4) tells the Vision LLM: *"trust the image over the OCR text when they disagree."* On a 2-page PDF invoice where line items continue on page 2 and the subtotal/tax/total are at the bottom of page 2, the Vision LLM only sees page 1's image, leading to hallucinated or missing totals.
  2. **Silent truncation of scanned PDFs (`_scanned_pdf`):**
     - In [`parsers.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/ingestion/parsers.py#L20-L21), `_pdf()` raises a clear `ValueError` if `len(doc) > get_settings().max_pdf_pages` (200).
     - In contrast, [`_scanned_pdf()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/ingestion/parsers.py#L30-L34) silently slices `range(min(len(doc), get_settings().max_ocr_pages))` (20 pages). A 50-page scanned PDF is marked `status = "indexed"` while **pages 21–50 are silently discarded** without an error or warning.
  3. **Hybrid PDFs skip OCR completely (`_pdf`):**
     - Line 24 checks `if not any(page.get_text().strip() for page in doc): return _scanned_pdf(doc)`. If a scanned PDF has even a single page with a digital header/footer, watermark, or cover sheet, `any(...)` is `True`, so `_scanned_pdf` is bypassed and every scanned page produces an empty string (`text.strip() == ""`), dropping almost the entire document.
- **Remediation:**
  1. For PDF receipts with `1 < len(doc) <= max_receipt_pages`, either pass multiple page images to `complete_structured_with_image` or stitch the rendered pages vertically into a single composite image before `for_llm()`.
  2. In `_pdf()`, decide OCR **per page** (`if not page.get_text().strip(): ...`) rather than all-or-nothing across the whole document, and either raise a clear `ValueError` when scanned pages exceed `max_ocr_pages` or record a warning visible to the user.

---

#### CR-03: Synchronous Upload Holds DB Transaction, `Tenant` Row Lock, and Advisory Lock Across S3 Network I/O
- **Severity:** `P1 (High)`
- **Category:** Concurrency / Database Connection Starvation / Storage Leak
- **Locations:**
  - [`src/rag/api/routers/documents.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/documents.py#L61-L125) (`store_upload`)
  - [`src/rag/adapters/storage.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/adapters/storage.py#L22-L31)
- **Analysis:**
  - In [`store_upload()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/documents.py#L70-L121):
    1. Line 70 acquires the transaction-level Postgres shared advisory lock (`await lock_index(db)`).
    2. Line 71 acquires an exclusive row lock on the `Tenant` row (`SELECT ... FOR UPDATE`).
    3. Line 118 executes `await get_storage().put(key, data, mime)` (network upload of up to 50 MB to S3/MinIO, configured with `connect_timeout=5, read_timeout=30, retries={"max_attempts": 3}` — up to **90 seconds** worst case) **before** `await db.commit()` at line 121.
- **Impact:**
  1. **Tenant Serialization & Pool Exhaustion:** Every upload for a tenant blocks all other concurrent uploads for that tenant (waiting on the `Tenant` row lock) for the full duration of the S3 transfer. With `pool_size=10, max_overflow=10` ([`session.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/db/session.py#L19-L26)), slow S3/MinIO I/O exhausts the API's SQLAlchemy connection pool across all endpoints.
  2. **Orphaned S3 Objects:** If `await db.commit()` at line 121 fails (e.g., transient DB error or serialization conflict), the object uploaded to S3 at line 118 is never cleaned up.
- **Remediation:**
  1. Upload to S3 **before** acquiring the `FOR UPDATE` lock and `lock_index(db)` (using a generated `doc_id` or staging key), or release the DB transaction before uploading to S3 and compensate with `await get_storage().delete(key)` in an `except` block if the subsequent DB commit fails.

---

#### CR-04: BFF Shared Container IP Enables Global Auth Lockout (`TRUSTED_PROXY_HOPS=0`) & Missing Per-Account Rate Limits on `reset_password` / `verify_signup`
- **Severity:** `P1 (High)`
- **Category:** Security / Availability (Denial of Service)
- **Locations:**
  - [`web/src/lib/security.ts`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/web/src/lib/security.ts#L25-L33) (`clientIp`)
  - [`web/src/app/api/auth/[action]/route.ts`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/web/src/app/api/auth/%5Baction%5D/route.ts#L25-L36) (`forwardIp`)
  - [`src/rag/auth/service.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/auth/service.py#L140-L158), [`service.py#L211-L213`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/auth/service.py#L211-L213), [`service.py#L252-L280`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/auth/service.py#L252-L280)
- **Analysis:**
  1. **Global BFF IP Collapsing:**
     - By default (`TRUSTED_PROXY_HOPS=0`), [`clientIp()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/web/src/lib/security.ts#L25-L33) returns `null`, so [`forwardIp()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/web/src/app/api/auth/%5Baction%5D/route.ts#L25-L36) sends **no `X-Forwarded-For` header** to FastAPI.
     - Consequently, FastAPI sees `request.client.host == "172.31.250.2"` (the `web` container's internal IP) for **every browser user**.
     - Because `rl_signup_per_ip = 5`, `rl_forgot_per_ip = 5`, `rl_reset_per_ip = 10`, and `rl_login_per_ip = 20` per hour ([`config.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/core/config.py#L51-L59)), a single user or attacker submitting 5 signups, 5 password resets, or 20 failed logins locks out **every user of the web application** for up to an hour. (Note: [`web/playwright.config.ts`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/web/playwright.config.ts#L7) even documents a workaround for this exact bottleneck in E2E tests.)
  2. **Missing Per-Token/Per-Email Rate Limit on `reset_password` and `verify_signup`:**
     - [`reset_password()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/auth/service.py#L279-L280) and [`verify_signup()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/auth/service.py#L212) only rate-limit by IP (`reset:ip:{ip}`, `signup-verify:ip:{ip}`). When an attacker rotates source IPs behind a proxy (`TRUSTED_PROXY_HOPS=1`), there is no global or per-account rate limit on `reset_password`.
- **Remediation:**
  1. In Next.js route handlers, fall back to the Next.js request peer socket address when `TRUSTED_PROXY_HOPS=0`, or require an ingress proxy in production `compose.yaml` with `TRUSTED_PROXY_HOPS=1`.
  2. Add a secondary rate-limit key on `reset_password` and `verify_signup` (e.g., keyed by token hash prefix or target `user_id` before completing the action).

---

### P2 — Medium Severity Findings

---

#### CR-05: `AnthropicLLM.complete` and `OpenAILLM.complete` Silently Ignore `temperature`; `OllamaLLM.stream` Hardcodes `temperature=0.2`
- **Severity:** `P2 (Medium)`
- **Category:** Correctness / LLM Adapter Contract
- **Locations:**
  - [`src/rag/adapters/llm/base.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/adapters/llm/base.py#L77-L84)
  - [`src/rag/adapters/llm/anthropic.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/adapters/llm/anthropic.py#L52-L87)
  - [`src/rag/adapters/llm/openai.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/adapters/llm/openai.py#L34-L78)
  - [`src/rag/adapters/llm/ollama.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/adapters/llm/ollama.py#L52-L77)
- **Analysis:**
  - The `LLM` Protocol in [`base.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/adapters/llm/base.py#L81) defines `temperature: float = 0.0` on `complete()` so orchestration, structured extraction, and judge calls are deterministic (`temperature=0.0`).
  - However, in [`AnthropicLLM.complete()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/adapters/llm/anthropic.py#L57-L65):
    ```python
    kwargs: dict[str, Any] = {
        "model": self.model,
        "max_tokens": max_tokens,
        "messages": chat,
    }
    ```
    `temperature` is **never added to `kwargs`**, so Anthropic defaults to `temperature=1.0` for all `complete()`, `complete_structured()`, and `complete_structured_with_image()` calls (including query analysis, receipt extraction, and evaluation judging!).
  - Similarly, [`OpenAILLM.complete()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/adapters/llm/openai.py#L39-L51) never passes `temperature` to `self._client.chat.completions.create(**kwargs)`.
  - Meanwhile, [`OllamaLLM.stream()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/adapters/llm/ollama.py#L66) hardcodes `"options": {"temperature": 0.2}`, while `AnthropicLLM.stream` and `OpenAILLM.stream` use `1.0`.
- **Remediation:**
  1. Pass `"temperature": temperature` in `AnthropicLLM.complete()`.
  2. Pass `"temperature": temperature` in `OpenAILLM.complete()` for non-reasoning models (or when `temperature` is supported by the configured model).
  3. Add `temperature: float = 0.2` to `LLM.stream()` so all providers use a consistent generation temperature.

---

#### CR-06: `FallbackLLM.stream` Applies a Single `timeout_s` Deadline to the Entire Stream and Discards Partial Answers
- **Severity:** `P2 (Medium)`
- **Category:** Reliability / UX / Streaming
- **Locations:**
  - [`src/rag/adapters/llm/router.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/adapters/llm/router.py#L123-L163)
  - [`src/rag/api/routers/chat.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/chat.py#L163-L176)
- **Analysis:**
  - In [`FallbackLLM.stream()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/adapters/llm/router.py#L133-L146):
    ```python
    async with asyncio.timeout(entry.timeout_s):
        first = await it.__anext__()
    ...
    yield first
    async with asyncio.timeout(entry.timeout_s):
        async for ev in it:
            ...
            yield ev
    ```
  - After the first token is yielded (`yield first`), `FallbackLLM.stream` cannot fall back (`yielded = True` at line 140). Wrapping the entire `async for ev in it:` loop in a single `asyncio.timeout(entry.timeout_s)` (60s in [`config/models.yaml`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/config/models.yaml#L24-L35)) means any detailed response that streams smoothly for 61 seconds is killed mid-sentence with a `TimeoutError`.
  - Furthermore, in [`chat.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/chat.py#L163-L176), when an exception occurs mid-stream, the assistant message is **never persisted**, so refreshing the conversation loses the entire streamed answer while leaving the user prompt saved in the database.
- **Remediation:**
  1. Apply a per-chunk inactivity timeout (`async with asyncio.timeout(chunk_timeout_s): ev = await it.__anext__()`) after the first token (with a generous overall ceiling, e.g., 300s).

---

#### CR-07: Early User Message Commit Leaves Consecutive `role="user"` Turns on Error/Cancel, Breaking Alternating-Role LLM APIs
- **Severity:** `P2 (Medium)`
- **Category:** Correctness / Concurrency
- **Locations:**
  - [`src/rag/api/routers/chat.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/chat.py#L81-L94), [`chat.py#L163-L176`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/chat.py#L163-L176)
  - [`src/rag/graph/graph.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/graph/graph.py#L144-L148) (`respond_direct`)
- **Analysis:**
  1. In [`chat()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/chat.py#L92-L93), the user's `Message(role="user", ...)` is inserted and committed **before** the LangGraph pipeline runs.
  2. If the user clicks **Stop** in the UI (`AbortController.abort()`), or the LLM times out / fails, no `assistant` message is committed.
  3. On the next turn in the same conversation, `history` loaded at lines 82–90 contains `user` → `user`.
  4. In [`respond_direct()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/graph/graph.py#L144-L148):
     ```python
     messages: list[Message] = [Message("system", _PROMPT_DIRECT)]
     messages += [Message(t.role, t.content) for t in state.get("history", [])[-4:]]
     messages.append(Message("user", state["question"]))
     ```
     This sends consecutive `"user"` messages to the LLM (`AnthropicLLM` Messages API requires strictly alternating `user`/`assistant` roles or rejects the request with `400 Bad Request`).
  5. Additionally, line 85 orders history by `Message.created_at.desc()` without the secondary `Message.id.desc()` tie-breaker used in [`get_conversation()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/chat.py#L260).
- **Remediation:**
  1. Either commit both the user and assistant messages atomically when the turn completes (or persist a stopped/failed assistant placeholder), **and** coalesce/filter consecutive same-role messages when constructing `messages` in `respond_direct()`.
  2. Add `Message.id.desc()` to the `ORDER BY` clause in [`chat()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/chat.py#L85).

---

#### CR-08: `reindex` Holds an Exclusive Advisory Lock Inside a Single DB Transaction Across the Entire Corpus Re-Embedding Loop
- **Severity:** `P2 (Medium)`
- **Category:** Concurrency / Availability
- **Locations:**
  - [`src/rag/ingestion/reindex.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/ingestion/reindex.py#L24-L70)
  - [`src/rag/ingestion/locking.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/ingestion/locking.py#L12-L21)
- **Analysis:**
  - [`docs/ARCHITECTURE.md`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/docs/ARCHITECTURE.md#L354) (`§4.5`) states: *"Collection `chunks_v{n}` accessed via alias `chunks_current` → zero-downtime re-embedding on model upgrade."*
  - However, [`reindex()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/ingestion/reindex.py#L25) acquires `await lock_index(db, exclusive=True)` (`pg_advisory_xact_lock(7241901)`) at the **very beginning** of the function and holds that transaction open while streaming every chunk in Postgres, calling the external embedding API (`embedder.embed(...)`), and upserting batches into Qdrant (`vs.upsert(...)`).
  - Because `store_upload()` ([`documents.py:70`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/documents.py#L70)), `delete_document()` ([`documents.py:181`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/documents.py#L181)), `ingest_document()` ([`pipeline.py:67`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/ingestion/pipeline.py#L67)), and `extract_receipt()` ([`extract.py:71`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/receipts/extract.py#L71)) all call `await lock_index(db)` (shared lock `pg_advisory_xact_lock_shared(7241901)`), **every upload, delete, and worker ingestion job hangs waiting on Postgres** for the entire duration of `reindex`.
- **Remediation:**
  1. Only acquire `lock_index(db, exclusive=True)` for the brief final step (verifying no in-flight chunks were written since the snapshot or re-indexing the delta since `started_at` and calling `vs.swap_alias(target)`), or move `lock_index(db)` out of the HTTP upload handler (`store_upload`) since `store_upload` only writes to Postgres/S3 and does not touch Qdrant or `chunks`.

---

#### CR-09: `delete_document` Synchronously Calls Qdrant Inside the HTTP Transaction Despite Enqueuing `purge_document`
- **Severity:** `P2 (Medium)`
- **Category:** Reliability / Consistency
- **Locations:**
  - [`src/rag/api/routers/documents.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/documents.py#L174-L202) (`delete_document`)
  - [`src/rag/ingestion/pipeline.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/ingestion/pipeline.py#L182-L189) (`purge_document`)
- **Analysis:**
  - In [`delete_document()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/documents.py#L181-L198):
    - Line 188 calls `await get_vectorstore().delete_document(ctx.tenant_id, doc_id)` synchronously inside the HTTP handler while holding `lock_index(db)` and `SELECT ... FOR UPDATE` on `Document`.
    - Line 191 then enqueues `enqueue(db, "purge_document", ...)` which calls `get_vectorstore().delete_document(...)` a second time in the worker ([`pipeline.py:185`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/ingestion/pipeline.py#L185)).
  - If Qdrant is briefly slow or unreachable during `DELETE /v1/documents/{id}`, the HTTP request fails with a 500 error even though soft-deleting the `Document` row (`status = "deleted"`) and letting `purge_document` clean up Qdrant and S3 asynchronously with retries is already implemented.
  - Conversely, if Qdrant deletes the vectors at line 188 and `db.commit()` fails at line 198, the document remains `status = "indexed"` in Postgres while its vectors are gone from Qdrant.
- **Remediation:**
  1. Remove the synchronous `await get_vectorstore().delete_document(...)` call from `delete_document()` (or wrap it in a best-effort non-blocking try/except **after** `await db.commit()`), relying on `status = "deleted"` + the transactional `purge_document` worker job for guaranteed eventual cleanup.

---

#### CR-10: `analyze_query` Omits `filters` Extraction (`§4.2`) and Qdrant Omits the `category` Payload Field & Index (`§4.5`)
- **Severity:** `P2 (Medium)`
- **Category:** Architecture / Specification Drift
- **Locations:**
  - [`docs/ARCHITECTURE.md`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/docs/ARCHITECTURE.md#L210), [`ARCHITECTURE.md#L356`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/docs/ARCHITECTURE.md#L356)
  - [`src/rag/graph/graph.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/graph/graph.py#L55-L58), [`graph.py#L106-L139`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/graph/graph.py#L106-L139)
  - [`src/rag/adapters/vectorstore.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/adapters/vectorstore.py#L79-L85)
  - [`src/rag/ingestion/pipeline.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/ingestion/pipeline.py#L129-L138)
- **Analysis:**
  1. [`ARCHITECTURE.md §4.2`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/docs/ARCHITECTURE.md#L210) specifies that `analyze_query` returns `{standalone_query, needs_retrieval, filters}` so queries like *"show me travel receipts"* or *"search only the receipt documents"* can filter retrieval. In [`graph.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/graph/graph.py#L55-L58), `QueryAnalysis` only defines `standalone_query: str` and `needs_retrieval: bool`.
  2. [`ARCHITECTURE.md §4.5`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/docs/ARCHITECTURE.md#L356) specifies Qdrant payload indexes on `tenant_id`, `doc_id`, and `category`. In [`vectorstore.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/adapters/vectorstore.py#L79-L85), only `tenant_id` and `doc_id` indexes are created, and [`ingest_document()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/ingestion/pipeline.py#L129-L138) / [`extract_receipt()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/receipts/extract.py#L156-L165) never write `category` or `doc_type` to the Qdrant point payload.
- **Remediation:**
  1. Include `doc_type` and `category` in the Qdrant point payload during ingestion/receipt extraction and create the `category` keyword index in [`VectorStore.ensure_collection()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/adapters/vectorstore.py#L79-L85).
  2. Either extend `QueryAnalysis` to extract optional metadata filters (merged with caller-supplied `filters`) or update `ARCHITECTURE.md` if filter extraction is intentionally caller-driven only.

---

#### CR-11: Service-Account API Keys with `Role.ADMIN` Are Blocked from Member & API-Key Management Routes
- **Severity:** `P2 (Medium)`
- **Category:** Architecture / RBAC Contract
- **Locations:**
  - [`docs/ARCHITECTURE.md`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/docs/ARCHITECTURE.md#L390-L433) (`§4.6` & `§4.7`)
  - [`src/rag/api/routers/members.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/members.py#L16)
  - [`src/rag/api/routers/apikeys.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/apikeys.py#L18)
  - [`src/rag/auth/service.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/auth/service.py#L462-L465)
- **Analysis:**
  - [`ARCHITECTURE.md §4.6`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/docs/ARCHITECTURE.md#L390) defines `X-API-Key` as *"Service-account keys with a role; same `require(...)` path"*, and `§4.7` maps `member:manage` and `apikey:manage` to `Role.ADMIN`.
  - However, both [`members.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/members.py#L16) and [`apikeys.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/apikeys.py#L18) pass `user_only=True` to `require(...)`, returning `403 This endpoint requires a user session` for admin API keys.
  - Note: Blocking API keys from creating other API keys (`apikeys.py`) is a reasonable privilege-escalation guard, and [`create_invitation()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/auth/service.py#L337) requires `invited_by: uuid.UUID` (`users.id` FK), whereas for an API key `ctx.user_id` is initialized to `row.id` (the `api_keys.id`, not `row.created_by`!) in [`deps.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/auth/deps.py#L105).
- **Remediation:**
  1. Document in `ARCHITECTURE.md §4.6 / §4.7` which routes are intentionally `user_only=True` (`members`, `invitations`, `api-keys`, `me`, `auth/switch-tenant`, `auth/password/change`).
  2. In [`deps.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/auth/deps.py#L100-L108), add an explicit `actor_id` / `api_key_id` separation on `RequestContext` rather than overloading `user_id` with `api_keys.id`.

---

### P3 — Low Severity Findings

---

#### CR-12: `DocumentsView` and `ReceiptUploads` Hardcode `limit=100` with No Pagination UI
- **Severity:** `P3 (Low)`
- **Category:** Frontend / UX
- **Locations:**
  - [`web/src/components/documents-view.tsx`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/web/src/components/documents-view.tsx#L31), [`documents-view.tsx#L219`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/web/src/components/documents-view.tsx#L219)
- **Analysis:**
  - Unlike [`ReceiptsView`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/web/src/components/receipts-view.tsx#L53), [`MembersView`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/web/src/components/members-view.tsx#L38), and [`SettingsView`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/web/src/components/settings-view.tsx#L148) (which all implement `offset`/`limit` pagination controls), `DocumentsView` and `ReceiptUploads` fetch `/api/v1/documents?limit=100` once and provide no pagination controls. Any workspace with >100 uploaded documents cannot see or delete documents beyond the 100 most recent.
- **Remediation:**
  - Add standard `offset` / `limit` pagination state and Next/Prev controls to `DocumentsView` and `ReceiptUploads`.

---

#### CR-13: Process-Local Refresh Deduplication Cache (`upstream.ts`) Prevents Multi-Replica Web Scaling Without Sticky Sessions
- **Severity:** `P3 (Low)`
- **Category:** Scalability / BFF Architecture
- **Locations:**
  - [`web/src/lib/upstream.ts`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/web/src/lib/upstream.ts#L50-L97)
- **Analysis:**
  - `refreshSession()` uses process-local `Map` objects (`inflight` and `recent` with a 10s grace window) so parallel browser requests with an expired access token share a single `/v1/auth/refresh` call.
  - If `web` is scaled to ≥2 replicas behind a round-robin load balancer without sticky sessions, two concurrent requests from the same browser tab can hit two different `web` replicas with the same `refreshToken`, causing the second request to trigger FastAPI's refresh-token reuse detection ([`service.py:424-431`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/auth/service.py#L424-L431)), revoking the user's entire token family and logging them out.
- **Remediation:**
  - Either allow a brief server-side grace window (e.g., 5–10s) on the just-rotated refresh token in [`auth/service.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/auth/service.py#L424-L431) before treating a duplicate refresh from the same IP/session as token theft, or enforce sticky sessions when running multiple `web` replicas.

---

#### CR-14: JWT Claims Hardening (`iss`/`aud`) & Existing-User Invite Acceptance Flow
- **Severity:** `P3 (Low)`
- **Category:** Security / Defense-in-Depth
- **Locations:**
  - [`src/rag/auth/tokens.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/auth/tokens.py#L22-L42)
  - [`src/rag/auth/service.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/auth/service.py#L361-L406) (`accept_invitation`)
- **Analysis:**
  1. `mint_jwt` / `verify_jwt` do not set or validate `iss` (issuer) or `aud` (audience) claims. While `JWT_SECRET` is dedicated to this service, adding `iss`/`aud` prevents cross-service token confusion if secrets are ever mistakenly shared.
  2. In [`accept_invitation()`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/auth/service.py#L376-L406), if `user` already exists (`user is not None`), calling `POST /v1/auth/invitations/accept` with `{token, password: ""}` adds the user to the inviting workspace and returns a valid JWT + refresh token for that user in the new workspace based solely on possession of the emailed invitation token. Because possession of the email inbox already allows password reset, this is not an elevation of privilege, but the UI/API contract should be explicit that possessing the email invite link signs an existing account into the invited workspace.

---

#### CR-15: RBAC Matrix Test Gaps & Disabled CI Evaluation Gate
- **Severity:** `P3 (Low)`
- **Category:** Testing / CI
- **Locations:**
  - [`tests/integration/test_rbac_matrix.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/tests/integration/test_rbac_matrix.py#L16-L39)
  - [`.github/workflows/ci.yml`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/.github/workflows/ci.yml#L171-L202)
- **Analysis:**
  1. `MATRIX` in [`test_rbac_matrix.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/tests/integration/test_rbac_matrix.py#L16-L39) tests 22 routes against `(viewer, editor, admin, api_key_viewer, unauth)` but omits 6 mutating routes:
     - `POST /v1/documents`
     - `POST /v1/receipts`
     - `DELETE /v1/invitations/{id}`
     - `PATCH /v1/me`
     - `POST /v1/auth/switch-tenant`
     - `POST /v1/auth/password/change`
     - And it only tests `api_key_viewer`, never `api_key_editor` or `api_key_admin`.
  2. In [`.github/workflows/ci.yml`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/.github/workflows/ci.yml#L175), the `eval` job is gated behind `vars.EVAL_ENABLED == 'true'` and runs against `EVAL_API_URL` without path filters (`ingestion/`, `retrieval/`, `graph/`, `config/models.yaml`) as described in `ARCHITECTURE.md §5.4`.

---

## 4. Outstanding Tasks Left Out (vs. `docs/ARCHITECTURE.md`)

Based on a line-by-line audit of [`docs/ARCHITECTURE.md`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/docs/ARCHITECTURE.md) (specifically `§0 Rev 2`, `§1`, `§4.2`, `§4.5`, `§4.10`, `§5.3`, `§5.4`, and `§8 Phases 0–6`), the following tasks remain unfinished:

### 1. Golden Evaluation Dataset Expansion (`§0 Rev 2 #8`, `§5.4`, `§8 Phase 0`)
- **Requirement:** *"Golden set (50–100 real questions) with expected source documents"* and Phase 0 exit criteria: *"Draft golden eval set (≥ 50 Q&A with expected docs)."*
- **Current State:** [`evals/golden.jsonl`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/evals/golden.jsonl#L1-L3) contains **only 3 sample rows** (2 RAG questions + 1 direct greeting), with placeholder `<DOC_UUID_...>` strings where `expected_doc_ids` is empty (`[]`).
- **Action Needed:** Build a deterministic seed corpus script (`evals/seed_corpus.py`) and populate 50–100 realistic multi-document and receipt Q&A pairs with populated `expected_doc_ids`.

### 2. Production Performance & Quality Gate Sign-Off (`§1`, `§8 Phase 6`)
- **Requirement:**
  - Query latency: **p95 TTFT < 3s**, **p95 total < 12s**
  - Retrieval recall: **Hit rate @ top-8 ≥ 0.85**
  - Answer faithfulness: **LLM-as-judge score ≥ 0.90**
  - Ingestion throughput: **100-page PDF searchable in < 2 min**
- **Current State:** Explicitly documented in [`docs/ARCHITECTURE.md`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/docs/ARCHITECTURE.md#L797) as **"Not yet met"**: local smoke run measured p95 TTFT ≈ 5.2s (local Ollama `qwen2.5:7b` on CPU/MPS), and the 100-page PDF ingestion benchmark and 50-question eval benchmark have not been executed and signed off on production-equivalent hardware.

### 3. Legacy Streamlit UI Retirement (`§8 Phase 5b`)
- **Requirement:** [`docs/ARCHITECTURE.md`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/docs/ARCHITECTURE.md#L787-L788) states: *"Next: retire the legacy Streamlit UI when no longer needed... Removal of legacy code remains outstanding."*
- **Current State:** [`ui/`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/ui/app.py) (`app.py`, `api.py`, `views.py`), `streamlit>=1.45` in [`pyproject.toml`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/pyproject.toml#L31), `COPY ui ./ui` in [`Containerfile`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/Containerfile#L25), and `CORS_ORIGINS=["http://localhost:8501"]` in [`config.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/core/config.py#L43) are still present and shipped in the production API/worker container image.

### 4. Query Filter Extraction & `category` Payload Index (`§4.2`, `§4.5`)
- **Requirement:** `analyze_query` should extract `{standalone_query, needs_retrieval, filters}` (`§4.2`), and Qdrant should index `category` alongside `tenant_id` and `doc_id` (`§4.5`).
- **Current State:** Neither `QueryAnalysis.filters` nor Qdrant's `category` payload field/index is implemented (see **CR-10**).

### 5. Alertmanager & Notification Channel Provisioning (`§5.3`, `docs/RUNBOOKS.md`)
- **Requirement:** Production alert routing for the 5 alert rules in [`ops/prometheus/alerts.yml`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/ops/prometheus/alerts.yml).
- **Current State:** Alert rules are loaded into Prometheus, but worker metrics are unscraped (**CR-01**) and no Alertmanager service or notification receiver configuration is defined in `ops/` or `compose.yaml`.

### 6. Rolling Conversation Summary for Long Chats (`§4.10`, `§8 Phase 5b`)
- **Requirement:** Optional rolling summary when conversations grow beyond the 4-turn sliding window (`§4.10`).
- **Current State:** Deferred in `§4.10` / `§8 Phase 5b` pending long-chat evaluation results.

---

## 5. Recommended Prioritized Remediation Plan

1. **Immediate (Before Any Production Traffic — Fix `P1` Issues):**
   - Expose worker Prometheus metrics on `:8001` in [`src/rag/worker/loop.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/worker/loop.py#L140) and add `worker:8001` to [`ops/prometheus/prometheus.yml`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/ops/prometheus/prometheus.yml) (**CR-01**).
   - Move `await get_storage().put(...)` outside the `SELECT ... FOR UPDATE` + `lock_index(db)` critical section in [`src/rag/api/routers/documents.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/api/routers/documents.py#L70-L121) and add compensating S3 delete on DB failure (**CR-03**).
   - Fix multi-page PDF receipt vision rendering in [`src/rag/receipts/ocr.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/receipts/ocr.py#L101-L108) and per-page OCR fallback in [`src/rag/ingestion/parsers.py`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/src/rag/ingestion/parsers.py#L24-L35) (**CR-02**).
   - Pass real client IP (or session-scoped fallback) from Next.js BFF and add per-account rate-limit keys on `reset_password` and `verify_signup` (**CR-04**).
2. **Short-Term (Next Sprint — Fix `P2` Issues):**
   - Pass `temperature` in `AnthropicLLM.complete` and `OpenAILLM.complete` (**CR-05**).
   - Switch `FallbackLLM.stream` from total-stream timeout to per-chunk inactivity timeout (**CR-06**).
   - Sanitize consecutive `user` messages in `respond_direct` and add `Message.id.desc()` tie-breaker (**CR-07**).
   - Narrow `lock_index(db, exclusive=True)` in `reindex()` to the alias-swap phase and remove synchronous Qdrant delete from `delete_document()` (**CR-08**, **CR-09**).
   - Add `category` / `doc_type` to Qdrant point payloads and `ensure_collection()` (**CR-10**).
3. **Pre-GA Sign-Off (Close Outstanding `ARCHITECTURE.md` Exit Criteria):**
   - Expand [`evals/golden.jsonl`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/evals/golden.jsonl) to ≥50 seeded Q&A pairs and run the full evaluation + Locust load test on target hardware to close Phase 6 exit criteria.
   - Remove the legacy `ui/` directory and `streamlit` dependency from [`pyproject.toml`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/pyproject.toml) and [`Containerfile`](file:///Users/ekanathareddyurivakili/Documents/GitHub/rag-mvp/Containerfile).
