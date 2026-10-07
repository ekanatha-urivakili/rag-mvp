# Principal review — 7 October 2026

Baseline: `24e4460c44de8d22be57c40afc3de4ea0e915251` on `main`. Checkout: `Documents/GitHub/rag-mvp`.

Scope covers FastAPI, Next.js/BFF, legacy Streamlit, authentication/RBAC, tenant isolation,
Postgres queries/schema/migrations, retrieval and model adapters, parsing/OCR, worker leases,
email, storage/vector consistency, containers, CI, tests, documentation, performance and operations.
This is a code review with regression and HTTP tests, not a penetration test or production approval.
The older [review](CODE_REVIEW.md) records a different baseline; its runtime claims are historical.

## Fixed findings

References below point to the corrected code. Severity reflects impact and prerequisites.

| ID | Severity | Evidence and failure | Fix / regression |
| --- | --- | --- | --- |
| R01 | High | `web/src/lib/env.ts:15`, `web/src/lib/security.ts:25`: forwarded identity was enabled for direct clients. Installed Next.js `base-server.js:567` preserves an existing X-Forwarded-For header, so arbitrary supplied IPs bypassed IP quotas. | Ignore forwarded identity by default; require explicit trusted ingress configuration. Use real IP validation rather than a permissive character regex. Compose now propagates the setting. Tests cover spoofed headers and malformed addresses. |
| R02 | Medium | `src/rag/api/routers/auth.py:43`: cookie refresh relied on SameSite, which permits sibling-origin requests within a site; its cookie alias also ignored the configured name. | Cookie fallback requires an explicit Origin in CORS_ORIGINS and uses the configured name. Explicit refresh-token bodies remain supported for server clients. Logout uses its authenticated JWT session family, avoiding ambient cookie selection. Integration checks rejected/missing origins and custom cookie names. |
| R03 | Medium | `src/rag/api/routers/auth.py:78`: public refresh had no application request quota, permitting unlimited DB lookups and rotation work. | Shared Postgres IP bucket, default 120 requests per 60 seconds, with Retry-After. Regression checks that failed requests consume the quota. |
| R04 | Medium | `web/src/app/api/v1/[...path]/route.ts:23`, `web/src/lib/security.ts:67`: BFF caps trusted declared lengths, oversized queries silently lost their filters, and non-chat calls ignored disconnects. | Count streamed bytes: JSON 16 KB, upload 30 MB; overflow returns 413. Overlong queries return 414. Combine disconnect and timeout signals for every proxy call. Route tests cover dishonest length/chunked data, query rejection and CSRF. |
| R05 | High | `src/rag/receipts/ocr.py:90`, `src/rag/ingestion/parsers.py:22`: a small PDF could specify enormous page dimensions, allocating a pixmap before any guard; page processing was unbounded. | Check raster dimensions against MAX_IMAGE_PIXELS before allocation; reject PDFs exceeding MAX_PDF_PAGES (200). Iterate only bounded OCR pages. Force RGB/no alpha so CMYK pages produce valid Pillow data. Tests exercise pre-allocation rejection, a real CMYK PDF and page limits. |
| R06 | Low | `src/rag/ingestion/sniff.py:52`: Pillow's extreme image-size exception escaped as a 500. | Map DecompressionBombError to UnsupportedMediaType; regression uses a lowered Pillow threshold. |
| R07 | Medium | `src/rag/ingestion/pipeline.py:161`: terminal worker exception details were stored in Document.error and sent to users by API and email. Provider/parser errors can include internal details outside regex redaction. | Generic user-facing failure; internal job/log diagnostics remain available. Output validation also hides older stored diagnostics, and the email renderer sanitizes older queued failures. Regressions cover new failures, old records and queued emails without a data migration. |
| R08 | Medium | `src/rag/adapters/storage.py:60`: S3 multi-object deletion can return HTTP success with per-key Errors; cleanup ignored that result and marked the purge complete. | Raise on per-key failures so existing job retries run. Unit test checks partial failure followed by success; deletion remains idempotent. |
| R09 | Medium | `src/rag/api/routers/chat.py:211`, `web/src/components/chat-thread.tsx:67`: timestamp-only message cursors skipped messages with equal timestamps, common for imported/batched history. | Stable ordering and cursor on (created_at, id); the UI passes both. Timestamp-only API clients remain supported. Integration pages through tied timestamps and proves every ID appears once. |
| R10 | Medium | `src/rag/api/routers/chat.py:148`: conversation deletion during generation could cause a foreign-key failure after SSE response headers were sent. | Lock/check the conversation before saving the response. A deleted conversation yields a safe SSE error and no fabricated done event. Integration exercises the missing conversation path. |
| R11 | Low | `.github/workflows/ci.yml:87`: repository variables were interpolated directly into shell command source. | Pass EVAL_TENANT_ID through the environment and quote its expansion. The eval CLI validates UUIDs. This reduces shell injection exposure if configuration is changed; repository variable management already requires privilege. |
| R12 | Low | `web/src/app/api/auth/[action]/route.ts:86`: inherited object properties such as toString were treated as auth actions and returned 503. | Check own properties before indexing the action map. Live HTTP test verifies 404. |
| R13 | Low | `tests/integration/conftest.py:104`: fixed tenant email addresses collided with retained Mailpit messages; the full suite failed on repeat runs. | Unique email per test tenant preserves the user's inbox and isolates delivery assertions. The complete suite passes against existing Mailpit state. |
| R14 | High | `ui/views.py:190`, `ui/views.py:253`, `ui/views.py:155`: legacy Markdown rendering allowed remote image requests from prompt-injected answers and source snippets, even with unsafe_allow_html disabled. | Render untrusted chat, citations, receipt fields/warnings and API-key labels as plain text. Incremental text streaming and canonical-answer replacement remain. Streamlit AppTest proves a hostile Markdown image remains text. Next.js retains rich Markdown with raw HTML/images removed; its regression checks scripts, images and javascript links. |
| R15 | Medium | `src/rag/api/routers/members.py:20`, `src/rag/api/routers/apikeys.py:22`: member, invitation and key lists loaded unbounded tenant records into API/legacy UI memory. | Cap pages at 100 with bounded offsets and deterministic ordering. Streamlit exposes page selection. Integration proves distinct pages and rejects oversized limits on all three endpoints. |
| R16 | Medium | `src/rag/auth/service.py:312`: a tenant administrator could enqueue arbitrary invitation email volume without a rate quota. Self-service signup makes administrative access inexpensive. | Shared tenant invitation bucket, default 20 attempts/hour, checked before enqueueing. Integration verifies 429 and that excess requests do not create email jobs. Account farming across many tenants still needs aggregate policy. |

Documentation also corrects forwarded-hop assumptions, unconditional refresh-safety claims,
signup/OCR scope, RustFS deployment, missing observability profiles and Testcontainers claims.
The old Web 2021 mappings remain explicitly labelled historical identifiers.

## OWASP API Top 10:2023 coverage

| Category | Reviewed controls / remaining limits |
| --- | --- |
| API1 Object authorization | Tenant filters in documents/receipts, conversation ownership and feedback joins; vector tenant filter plus authoritative Postgres version/status validation. Existing isolation tests pass. |
| API2 Authentication | Argon2id, dummy verification, pinned JWT algorithm/claims, live session-family checks, refresh rotation/reuse revocation, password-reset invalidation; R01–R03. MFA and SSO are deferred. |
| API3 Property authorization | Strict Pydantic/Zod input objects and explicit output DTOs; no user-supplied role/tenant override on signup. |
| API4 Resource consumption | Shared quotas, actual-byte caps, DOCX expansion/image limits, R04/R05, bounded conversation/document/receipt/audit results, provider timeouts/token limits and bounded graph. Aggregate spend/storage/concurrency quotas remain absent. |
| API5 Function authorization | Permission dependencies, human-only admin actions, DB roles on each request, last-admin/self-change guards. Route inventory and RBAC matrix tests pass. |
| API6 Sensitive business flows | Email verification, generic signup/reset initiation, single-use links and request quotas, including invitation emails (R16). An invited existing user intentionally authenticates through email possession. Aggregate email budgets and anti-bot/account-farming controls remain deployment/product work. |
| API7 SSRF | No uploaded or model-supplied URL fetching; HTML parsed as bytes. Fixed server-configured BFF origin with method/path allowlist, no traversal or redirect following. |
| API8 Configuration | Host/CORS allowlists, production guards, API docs disabled in prod, headers, localhost ports, non-root apps; R01/R02. HTTPS/edge configuration must be verified per deployment. |
| API9 Inventory | Router coverage test and BFF allowlist. Legacy Streamlit remains a separate surface requiring its own ingress policy. |
| API10 Upstream consumption | Provider structured-output validation, exception boundaries, canonical citation validation and storage result checking (R08). Provider data is untrusted; model schemas and a valid citation do not establish truth. |

## OWASP Web Top 10:2025 coverage

| Category | Evidence / outcome |
| --- | --- |
| A01 Access control | DB-derived permissions, owner/tenant filters, exact-origin BFF mutations, cookie refresh origin check. CSRF is separate from CORS and SameSite; sibling origins are tested. |
| A02 Security configuration | Nonce CSP, frame denial, nosniff, referrer policy, production-only HSTS/Secure cookies, fixed upstream URL; ingress trust is now opt-in. Legacy UI lacks application CSP headers. |
| A03 Supply chain | Frozen Python and npm lockfiles, runtime dependency audits and CI. Container/action/model artifacts are not fully digest-pinned or scanned in this review. |
| A04 Cryptography | Argon2 password hashes, random opaque tokens stored as hashes, constant-time comparison, algorithm-pinned JWTs, authenticated JWE session cookies. TLS and secret-manager configuration need deployment evidence. |
| A05 Injection | Bound SQL parameters, yaml.safe_load, no user-derived storage paths, escaped email HTML/subjects, safe Next.js Markdown and plain legacy untrusted text. Prompt delimiter hardening is not prompt-injection immunity. |
| A06 Insecure design | BFF token custody and authoritative API authorization; retrieval has no side-effecting tools. Signup/account farming, aggregate spend and provider-consent policy remain business constraints. |
| A07 Authentication | Rotation/reuse/session revocation tests pass, plus refresh quotas. Multi-process web refresh requires coordination; passwords and tokens are never returned in validation errors. |
| A08 Integrity | Transactional job outbox, deterministic vector IDs, fenced worker row locks, authoritative ready-version retrieval, storage partial-delete detection. No cross-system atomic commit is claimed. |
| A09 Logging/alerting | Query-free access logs, request IDs, secret redaction, audit events and internal job errors. Automated alerts/retention and production proxy logs are not configured here. |
| A10 Exceptional conditions | Generic backend/BFF errors, bounded requests, controlled image rejection, safe deletion-during-stream response, retries after partial deletion. Process crashes/native parser faults require runtime isolation. |

## Performance, architecture and operations

The graph, adapters and API permissions are proportionate to this MVP. No speculative CQRS,
service split or dependency-injection rewrite is needed. SQL is parameterized, retrieval checks
derived state against Postgres, chat history is bounded, and blocking password/OCR/storage work
runs off the event loop. Reindex streams batches under an exclusive advisory lock; ingestion and
deletion hold locks around database/vector work. That favors correctness but can delay writes and
requires load evidence before increasing traffic. Uploads retain at most the configured file size
in memory; aggregate concurrent memory consumption still depends on admission limits.

Remaining production work, with concrete evidence:

- **DDoS and slow clients:** app quotas do not absorb volumetric attacks or stop many concurrent partial uploads. Ingress must bound connections, body/header sizes, read/idle time and rate; use the hosting network's DDoS controls. `src/rag/api/main.py:111` enforces body size only as data arrives. No adversarial load test was run.
- **Native parsers:** PDFs/DOCX/OCR still execute in worker threads, not killable sandbox processes (`src/rag/ingestion/pipeline.py:83`). Page/pixel limits do not bound every decompression or native-code failure. Use constrained worker processes/container memory/CPU limits for untrusted public uploads.
- **Economics and growth:** no aggregate tenant storage/document/token budget. Admin lists are now bounded (R15), but offset pages can move under concurrent edits. Define aggregate limits/retention with product requirements. Authenticated tenant administrators can still create sustained data workloads and email volume within configured quotas.
- **Refresh scaling:** `web/src/lib/upstream.ts:54` coordinates within one process. Sticky routing alone does not coordinate workers within a multi-process replica or failover. Use a single web process until shared coordination is added; validate the actual routing scheme before scaling.
- **In-flight authorization:** revocation prevents later API requests; already-running model calls may complete with context authorized at their start. Product policy must define cancellation expectations. Concurrent turns in different tabs may interleave one conversation's history.
- **Privacy and retention:** cloud fallback can transmit document content/images; OCR masks card numbers in text but original images are still uploaded/sent to vision routes. No tenant-specific local-only consent policy or backup deletion proof exists. Terminal job diagnostics and old raw Document.error values remain sensitive operator data, though API/email output now masks old document errors. Retention and at-rest cleanup need explicit policy.
- **Availability and delivery:** no backup/restore drill, production rollback exercise, remote provider smoke test, image/OS scan or calibrated retrieval/load benchmark. Floating container/action/model versions require release provenance. No production deployment or merge is part of this PR.

## Validation

- Full backend suite against isolated local Postgres/Qdrant/RustFS/Mailpit: **135 passed** after the final regressions; no test data truncation of the application database. Known PyMuPDF deprecation and local HTTP Qdrant warnings are not failures.
- Frontend: **46 tests passed**; ESLint, generated-route TypeScript checks and Prettier pass.
- Backend: Ruff lint/format and strict mypy pass.
- Production build: `next build --webpack` passes. Default Turbopack encountered an environment restriction while binding a compiler subprocess port (`Operation not permitted`); remote CI runs the normal build command.
- Live HTTP against the reviewed production frontend on a separate local port: login 200, CSP nonce matches rendered scripts, nosniff/DENY/no-referrer headers, cross-origin auth 403, oversized auth 413, inherited auth action 404, anonymous BFF 401.
- Browser discovery returned no connected browser. No visual, interactive browser CSP enforcement or browser XSS execution claim is made; render tests and HTTP headers are separate evidence.
- Pinned runtime audits: Python pip-audit reported no known vulnerabilities; npm audit reported zero vulnerabilities. These do not cover OS/image/model artifacts or guarantee absence of vulnerabilities.
- PR checks are the source for remote CI status. Local results do not establish deployment health or retrieval quality.

Sources consulted: [OWASP API Top 10:2023](https://api-security.owasp.org/editions/2023/en/0x11-t10/),
[OWASP Web Top 10:2025](https://top10.owasp.org/2025/),
[CSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html),
and [denial-of-service guidance](https://cheatsheetseries.owasp.org/cheatsheets/Denial_of_Service_Cheat_Sheet.html).
