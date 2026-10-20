# PR #20 principal review

Reviewed the PR diff and its integration with current `main` on 20 October 2026. Scope: security and privacy, authorization and tenant boundaries, correctness and concurrency, performance and resource use, database migrations, recovery procedures, observability, frontend authentication and accessibility selectors, dependency security, CI and regression coverage.

## Confirmed findings and fixes

| Priority | Finding | Remediation |
| --- | --- | --- |
| P1 | Automatic HTTP tracing included query strings; exception events/status descriptions could contain model output or document text. | `src/rag/core/telemetry.py:41`: filter at export to approved metadata, drop event/link payloads and status descriptions, name server spans by route template. Actual FastAPI exception/404 regressions verify sensitive strings are absent. |
| P1 | `verify` dropped the fixed database `rag_restore_check`, potentially deleting an existing database or another concurrent verification. | `scripts/backup.py:245`: use unique scratch databases/collections and delete only the database successfully created by this invocation. |
| P1 | Object validation compared only file count/total bytes; damaged artifacts could pass, and restore could modify Postgres before discovering a missing index or upload. | `scripts/backup.py:94`: inventory and SHA-256 validation of every artifact before external access; reject path escapes. New backup directories use mode 0700. |
| P1 | Counts and scratch SQL used `DATABASE_URL`, while dump/restore tools unconditionally used the Compose server, allowing mixed or unintended targets. | `scripts/backup.py:66`: compare cluster identifiers before dump/verification/restore; preserve URL options when constructing scratch URLs. Document the Compose-only database restriction. |
| P1 | Explicitly empty `METRICS_TOKEN` matched a missing Authorization header; a bare token also worked without Bearer authentication. | `src/rag/core/config.py:126`, `src/rag/api/routers/health.py:51`: reject blank configuration and require the Bearer scheme with constant-time token comparison. |
| P2 | Unhandled HTTP exceptions bypassed the new request counters, hiding real 500s. | `src/rag/api/middleware.py:39`: count failed requests and duration before rethrowing to the existing error handler. |
| P2 | Arbitrary HTTP method strings became unbounded Prometheus label values. | `src/rag/api/middleware.py:29`: normalize unknown methods to `OTHER`; preserve route templates/`unmatched`. |
| P2 | Delayed online evaluation chose the latest preceding user message, which could belong to an overlapping chat turn. | `src/rag/api/routers/chat.py:101`, `src/rag/evaluation/judge.py:71`: persist and resolve the original question ID within the same conversation. Skip legacy jobs without that association. |
| P2 | Judge jobs could resend soft-deleted/superseded context and fail on retry after the grade had committed. | `src/rag/evaluation/judge.py:53`: require live current-version tenant documents, skip existing grades, and use conflict-safe insertion. |
| P2 | The 24-hour terminal-failure gauge used job creation time, omitting an old job that failed today. | `src/rag/worker/loop.py:74`, `src/rag/core/metrics.py:63`: record/query `finished_at`; migration `0007` adds the column/index and approximates historical completion from the last claim. |
| P2 | Cancellation before the first stream event left its span open; closing the wrapper did not explicitly close the provider iterator. | `src/rag/adapters/llm/router.py:121`: use one cleanup path for span/iterator lifetime, including first-read cancellation and fallback. |
| P2 | The load gate could pass with no successful chats or with HTTP/SSE failures; truncated streams with some text counted as successful. | `loadtest/locustfile.py:52`: require successful observations, zero failures, the latency target and a terminal `done` event; bound stream reads. Six focused regressions run in CI. |
| P2 | Health/scrape traffic and unrelated jobs diluted the API error ratio; `increase()` missed a first-seen exhausted-route counter. | `ops/prometheus/alerts.yml:14`: scope application traffic consistently with Grafana. `ops/prometheus/alerts.yml:43`: alert on the latest exhaustion timestamp, including its first observation. Promtool tests cover detection and expiry. |
| P2 | Merged password visibility buttons made signup selectors ambiguous; the chat assertion allowed 120 seconds inside a default 30-second test. | `web/e2e/admin.setup.ts:22`, `web/playwright.config.ts:9`, `web/e2e/chat.spec.ts:7`: exact accessible labels and explicit assertion/test budgets. |
| P1 | The locked Pillow 12.3.0 had eight known vulnerabilities. FastEmbed's declared upper bound prevented the fixed release. | `pyproject.toml:56`: explicitly approved, exact Pillow 13.0.0 override after real BM25, cross-encoder and image compatibility checks. `scripts/check_model_compatibility.py:1` reproduces those checks. |

The [OpenTelemetry instrumentation documentation](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/fastapi/fastapi.html) describes the automatic request/exception instrumentation used here. The privacy regression exercises that instrumentation, rather than relying solely on manually constructed spans.

## Validation

- Backend: 189 tests passed (95 unit, 94 integration), including overlapping questions, retry idempotency, deleted context, recent failure of old jobs, metrics access, trace privacy, stream cleanup and backup safety. Ruff lint/format and strict mypy passed. Migration `0007` applied to the isolated test database.
- Frontend: lint, type generation/typecheck, formatting, 72 unit tests and production Webpack build passed.
- Browser: all 10 E2E tests passed against source API/worker and the production frontend on temporary ports 8005/3005, including live local chat streaming/persistence, signup, login/logout, viewer restrictions, API-key lifecycle, CSP and CSRF checks. Temporary source services were stopped afterward.
- Load test: six gate/parser regressions passed. This is not a production latency benchmark.
- Prometheus: configuration and all 11 rules validate; synthetic regressions pass for application-error dilution and first-observation exhaustion/expiry. CI now runs Promtool.
- Recovery: backed up isolated test data and restored Postgres/Qdrant into unique scratch targets; all 17 table counts, index point count and 108 upload files/87,840 bytes matched. Integrity checks passed. The live overwrite command was not run: automatic approval review rejected that drill because target isolation had not been established. Scratch-only verification provided the recovery evidence.
- Model/dependency checks: real BM25 embeddings, BAAI cross-encoder inference and PNG/JPEG/WebP upload validation/decoding pass with Pillow 13.0.0 and FastEmbed 0.8.1. Locked Python production audit and npm production audit report no known vulnerabilities.

## Operational limits

Run `alembic upgrade head` before starting the updated API/worker. Historical `finished_at` values use the last claim timestamp because exact completion times were never stored. Old queued judge jobs without a question ID are deliberately skipped.

Backups made before the integrity manifest must be recreated. Quiesce API/web/worker writes for a consistent backup across Postgres, Qdrant and uploads; only the Postgres snapshot is transactionally consistent. Full multi-store restore is not atomic. This review verified scratch recovery, not production restoration.

The local observability profile evaluates rules; Alertmanager, notification receivers and delivery remain deployment work. Production latency/load targets, production deployment, provider billing behavior and broad visual/WCAG certification were not established by these checks. The Pillow override covers the application's text-embedding/reranking and image-upload paths; remove it once FastEmbed supports the fixed release, and rerun the compatibility script when changing either package.
