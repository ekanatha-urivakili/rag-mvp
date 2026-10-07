# Full-stack review — RAG MVP

Reviewed 6 October 2026 against baseline `527130a` and the working-tree fixes in this review.
Scope: API, Streamlit, identity/RBAC, tenant isolation, retrieval/LLM graph, ingestion,
Postgres/Qdrant/S3 consistency, background jobs/email, containers, CI, dependencies and documentation.

## Assessment

The architecture is proportionate for an MVP: a bounded graph, thin provider adapters,
Postgres as the source of truth, a transactional jobs outbox, and tenant-level permissions.
CQRS frameworks, extra services and a React rewrite would not address the defects found here.
The initial passing tests did not establish correctness under concurrent requests, stale index
state, worker lease expiry, or access-token revocation. The findings below were fixed in code.
This remains a locally validated MVP, not a production sign-off.

## Fixed findings

References point to the current implementation. Severity describes the original behavior.

| ID | Severity / area | Evidence and impact | Fix |
| --- | --- | --- | --- |
| F01 | High / secrets | `compose.yaml:44`: UI inherited the backend `.env`, including JWT, provider and storage credentials, contradicting the stated UI boundary. | UI overrides `env_file` and volumes and receives only its API address. |
| F02 | High / authentication | `src/rag/auth/deps.py:40`: logout/replay revoked refresh records, but issued access tokens continued to authorize requests until expiry. | JWT `sid` references its refresh family; authentication checks a live, unexpired family record. Logout works without supplying a refresh token. Tests verify old and rotated access tokens fail after replay. |
| F03 | High / resource limits | `src/rag/api/main.py:110`: the original request cap trusted Content-Length, allowing chunked multipart data to be spooled before the route's file cap. | Installed Starlette body-limit middleware counts actual bytes. Real FastAPI chunked-upload regression returns 413, with security headers. |
| F04 | High / document lifecycle | `src/rag/ingestion/pipeline.py:32` and `src/rag/retrieval/retriever.py:38`: ingestion could race deletion/replacement; partial, failed or old vectors were trusted as answer context. | Document row locks serialize ingestion with mutations. Retrieval verifies ready status, tenant and exact version in Postgres before reranking/generation. |
| F05 | Medium / upload concurrency | `src/rag/api/routers/documents.py:62`: two new uploads under the same title could each create a document because a missing row cannot be locked. | A tenant row lock serializes upload identity/version decisions. Concurrent different-content uploads now produce one document at version 2. |
| F06 | High / index consistency | `src/rag/ingestion/reindex.py:19`: an incomplete target was reused on retry, live writes could be lost at alias swap, all chunks were loaded into memory, and the prior collection was dropped immediately. | Rebuild inactive targets, coordinate mutations through shared/exclusive Postgres locks, stream bounded batches, and retain the previous collection. Real Qdrant regression verifies ghost removal and retention. |
| F07 | High / test isolation | `src/rag/adapters/vectorstore.py:60`: a test alias still pointed at the same `chunks_v1` physical collection used by the running application. | Custom aliases get separate physical collection names; tests use `chunks_test_vN`. The application keeps `chunks_vN`. Existing application collection contents are not deleted. |
| F08 | High / worker concurrency | `src/rag/worker/loop.py:83`: any job running past the 15-minute lease could execute simultaneously in another worker. Finish/fail updates had no ownership fence. | Keep a dedicated row-lock transaction while a separate session executes the handler; check the claimed attempt before processing. Crashed jobs remain reclaimable and exhausted attempts fail. Regression forces immediate lease expiry and proves one execution. |
| F09 | Medium / password correctness | `src/rag/api/schemas.py:17`: global whitespace stripping silently altered signup/invite/reset/login passwords. | Normalize text fields while preserving password bytes. Regression covers leading/trailing spaces. |
| F10 | High / reset races | `src/rag/auth/service.py:65`, `src/rag/auth/service.py:89`, `src/rag/auth/service.py:276`: password reset could race login/refresh and leave a fresh session; other previously-issued reset URLs remained usable. | Lock the user consistently before session/password changes; consume reset tokens after acquiring that lock, invalidate other reset URLs, and revoke session families. |
| F11 | Medium / administration | `src/rag/auth/service.py:405`: checking the last-admin count under separate member locks allowed concurrent demotions/removals to violate the invariant. Permission context could become stale while waiting. | Serialize with a tenant lock and recheck the acting admin's current membership inside that transaction. |
| F12 | High / local runtime | `config/models.yaml:10`: live chat after successful ingestion hit the eight-second orchestration timeout despite startup preloading. | Local timeout is 30 seconds; startup awaits bounded warmup. Model eviction means startup preload alone is insufficient. |
| F13 | Medium / readiness and configuration | `src/rag/api/routers/health.py:20` and `src/rag/core/config.py:115`: readiness ignored object storage and treated required local Ollama as optional; production guards did not match README claims. | Check storage and required Ollama reachability; reject wildcard hosts, insecure CORS origins, missing Qdrant authentication and default S3 secrets in production. |
| F14 | Medium / logging | `src/rag/core/logging.py:40`: structured extra fields bypassed the redaction applied to messages and exceptions. | Redact the complete serialized JSON record; regression proves structured password/token values do not appear. |
| F15 | Medium / throttling | `src/rag/core/ratelimit.py:21`: truncating bucket keys at 200 characters merged distinct long email addresses. | Hash overlong keys; integration test verifies independent buckets. Signup, verification, reset completion and invitation acceptance are also throttled. |
| F16 | Medium / parsing | `src/rag/ingestion/sniff.py:41`: decoding a sliced UTF-8 prefix rejected valid text when a multibyte character crossed byte 4096. | Validate the full bounded text before extracting its prefix. Boundary regression passes. |
| F17 | Medium / answer correctness | `src/rag/graph/graph.py:177` and `ui/views.py:201`: citation cleanup only changed stored text; the displayed stream retained invalid indices. Uncited factual answers were accepted. | Emit a canonical `answer` SSE event and replace provisional UI text; uncited document answers become insufficient-context responses. Markers of any numeric length are checked. |
| F18 | Medium / UI resilience | `ui/api.py:66`, `:90` and `ui/app.py:15`: cached roles survived backend demotion, transport errors escaped as render failures, and a transient /me error destroyed the session. | Reload identity per full rerun, keep backend authorization authoritative, show an actionable service error, and clear credentials only for authentication failures. |
| F19 | Medium / recovery | `src/rag/api/routers/documents.py:73`: idempotent re-upload of a failed document always returned its failed state, with no way to retry. | Re-upload queues ingestion again for the same document/version. Regression verifies a new job. |
| F20 | Low / resources and latency | `src/rag/adapters/storage.py:49` and `src/rag/auth/service.py:74`: S3 response bodies were not explicitly closed; Argon2 work ran on the event loop. | Close response bodies using their context manager and run password hashing/verification in the thread pool. |

## Signup delivered

`ui/views.py:18` adds signup to sign-in. `POST /v1/auth/signup` accepts only an email
and returns a generic 202. A verification email carries a hashed-at-rest, single-use,
30-minute token. `POST /v1/auth/signup/verify` chooses the password and workspace name,
then atomically creates the user, private tenant, admin membership, audit event and session.
It neither enrolls the user into another tenant nor changes an existing account.
Unique name suffixes satisfy the existing global tenant-name constraint.

Migration `0002` extends the email-token type constraint; no password or signup draft is
stored in email jobs. `SIGNUP_ENABLED=false` disables initiation and completion.
The Streamlit form checks repeated passwords, exposes API errors, and signs in after success.
Existing invitations continue to work.

## Security and privacy review

Tenant and conversation-owner filters, bound SQL parameters, role dependencies, content
sniffing, bounded uploads, Argon2id, pinned JWT algorithm/claims, hashed refresh tokens,
non-root app containers, and transactional email enqueueing are implemented controls.
The role matrix now rejects unexpected server errors instead of counting 500 as an allowed request.
Public signup routes are explicitly added to the route-coverage allowlist.

The OWASP mapping is a control inventory, not certification or proof that all attack classes
are closed. In particular, delimiter neutralization and one successful injection example do
not prove prompt-injection immunity. Valid citation indices establish source attribution,
not support for every claim. Final citation cleanup happens after provisional token streaming;
API clients must consume the canonical `answer` event.

Existing invitations intentionally act as single-use email login links for existing accounts.
They do not require an existing password; this is an email-possession authentication path
and should be considered when adding MFA or stricter enterprise authentication.

Standards checked: [OWASP authentication guidance](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html),
[OWASP email verification guidance](https://cheatsheetseries.owasp.org/cheatsheets/Email_Validation_and_Verification_Cheat_Sheet.html),
and [Starlette body-limit middleware](https://www.starlette.io/middleware/).
Installed Starlette source was checked before adopting its middleware.

## Production considerations — outside this MVP implementation

| Area | Current limit / deployment action |
| --- | --- |
| Host Ollama | The existing host listener on all interfaces remains unauthenticated. Restrict network access before exposing this setup beyond localhost. Rebinding it blindly would break the configured container/GPU path; this review did not change host networking. |
| UI ingress | FastAPI headers do not protect Streamlit responses. Deploy UI and API behind HTTPS with suitable UI headers, websocket support, request limits, timeouts and query-string redaction. Token removal from the UI URL cannot remove earlier proxy/browser history entries. |
| Rate-limit identity | API IP limits see the Streamlit server's address, so browser users share that bucket. Email/user/tenant buckets still apply. Public scale requires a deliberately trusted client-identity boundary and tuned quotas; do not blindly trust arbitrary X-Forwarded-For. |
| Resource economics | Email verification and rate limits bound signup attempts; they are not a billing/tenant quota system. There are no per-tenant total storage, document-count, token-spend or retained-chat quotas. Long chats/member/key lists still require pagination/retention work at scale. |
| Model privacy | Routes are deployment-wide. Adding provider keys can send retrieved text to cloud providers. Tenant-specific local-only routing/consent controls are not implemented; the misleading configuration comment was corrected. |
| Embedding changes | Changing model or dimension requires planned query downtime and matching worker/API configuration. A safe index swap does not provide dual-model query compatibility. Retired indexes and raw versions need a retention/cleanup policy. |
| Durability | No backup/restore exercise, disaster recovery, retention deletion across backups, or availability/load test was run. Coordinate schema upgrades for a replicated production API. |
| Images / supply chain | `uv.lock` pins Python dependencies, but base/service/tool images use floating tags. Promote reviewed image digests and scan OS packages/model artifacts before release; the Python audit does not scan those layers. |
| Cloud providers | Claude/OpenAI credentials were absent. The Sonnet route, beta refusal fallback and provider-specific streaming/structured-output behavior were not live-tested. |
| Quality and performance | No calibrated retrieval benchmark, LLM judge run, adversarial prompt suite or concurrent load test was completed. The architecture's p95/recall/faithfulness/100-page ingestion targets remain unproven. |
| Observability | JSON logs and message usage/debug fields exist; OpenTelemetry/Langfuse and operational dashboards/alerts remain deferred. A Postgres checkpointer is unnecessary for the current graph's reload-per-turn behavior, but would matter for resumable workflows. |
| Delivery | CI (lint, strict mypy, full pytest, pip-audit) runs on every PR. CI does not establish release approval or a production deployment. |

## Validation evidence

- Ruff lint and formatting: pass across `src`, `tests`, `ui`, `evals`.
- Strict mypy: pass for all backend source files. UI/evals are linted; backend strict typing does not imply strict typing of those directories.
- Pytest: the full unit + integration suite runs in CI against real isolated Postgres, Qdrant, S3-compatible storage and Mailpit; the PR checks hold the current count.
- New tests cover verified signup/email/login/tenant isolation, generic existing-account response,
  expired/reused tokens, signup disabling/rate limits, password preservation, actual chunked
  request limits, structured redaction, UTF-8 boundaries, concurrent uploads, stale-vector
  filtering, failed-upload retry, live-worker lease fencing, real reindex recovery, and immediate logout/replay revocation.
- Streamlit AppTest: signup submission, mismatched password and expired-link rendering pass.
- Current Python dependency audit: **no known vulnerabilities found**, auditing pinned installed packages, including development dependencies, with `pip-audit --strict --disable-pip --no-deps`.
- Running API/worker/UI users are UID 10001; the UI environment contains none of the backend credential variables. UI health returns `ok`.
- Podman image build succeeds; API migration `0002` applied to the existing local application database. API, worker and UI were recreated without restarting infrastructure. `/readyz` reports Postgres, Qdrant, Ollama and storage healthy.
- Live signup → Mailpit verification → private-workspace admin → login succeeds. Initial live Markdown ingestion completed in **4.1 seconds**.
- Final image smoke test: signup and login pass; Markdown ingestion completed in **2.1 seconds**; the support-contact question and macOS follow-up both returned `[1]` citations to the uploaded document. The canonical answer exactly matched stored history. This is a functional smoke test, not a faithfulness score. Both temporary review workspaces/accounts and their document/index/object-storage content were cleaned up; the existing demo tenant was preserved.
- Browser discovery returned no connected browser; no visual or browser click-through claim is made.

`.env` is not changed or committed.
