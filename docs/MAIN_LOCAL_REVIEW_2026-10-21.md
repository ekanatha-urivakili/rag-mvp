# Local review of main — 21 October 2026

Reviewed commit `70c549dc7437f7f6199d0a9798312a6dd2afacfd` (PR #20) against the current local Podman stack. The working tree was clean before validation. No application-code defect was confirmed in the exercised flows.

## Finding and change

The opt-in browser chat regression checked the user's prompt after reload, but did not assert that the assistant answer survived. It could miss an empty or missing persisted answer. `web/e2e/chat.spec.ts` now requires exactly one nonempty rendered answer, compares it before and after reload, and checks that feedback controls remain available for the persisted message.

## Validation

- Python: 95 unit tests and 94 integration tests passed; Ruff lint/format and strict mypy passed. Integration tests use `rag_test`, separate from the running app database.
- Web: 72 unit tests, ESLint and type generation/typecheck passed. The updated E2E file passes Prettier and ESLint. Both container images built successfully, including the production Next.js build.
- Automated browser suite: all 10 tests passed in the final run with `E2E_CHAT=1` (25.1 seconds). Covers verified signup, login/logout, incorrect password, live local-model chat and answer persistence, viewer invitations/restricted navigation, API-key creation/dismissal/revocation, protected routes, security headers, CSRF and proxy allowlisting. This suite uses Playwright's bundled Chromium.
- Actual Google Chrome: signed into a disposable E2E workspace, sent a greeting to `qwen3.5:4b` through the UI, confirmed the completed answer and permanent conversation URL, reloaded and confirmed the answer persisted, and saved positive feedback. Documents, receipts and usage pages loaded. These page checks did not upload or extract a receipt/document.
- Running API: `/readyz` returned 200 with Postgres, Qdrant, Ollama and storage all `ok`; the app database reports migration `0007`.
- Observability profile: Prometheus, Grafana and Jaeger started. Prometheus reported its API target `up`, `/metrics` returned 200, configuration/11 alert rules validated, and the Promtool regression fixtures passed. OTLP export was not configured for this run; live trace export and notification delivery were not verified.
- Recovery: the new backup command and scratch-only restore verification passed. All 17 table counts (268 rows), Qdrant point count (40), and object inventory/checksums (4 files, 7,592,363 bytes) matched. No live overwrite restore was performed. Writes were not quiesced, so this does not establish a coordinated multi-store recovery point.

## Runtime observations and limits

Podman initially refused connections; a terminal restart failed to connect to its `gvproxy` socket. The user started the runtime manually. The launcher subsequently built and started the app successfully.

Ollama started by the launcher was unavailable after the launcher session ended. The first automated run passed nine non-model checks and failed its chat URL assertion. Running Ollama in a persistent loopback-bound terminal session restored container connectivity and readiness; actual Chrome chat and the full automated rerun then passed. This establishes a local runtime interruption, not a confirmed application regression or reliable unattended model startup.

Production deployment, sustained load/p95 latency targets, cloud-provider fallback behavior, live OTLP export, alert delivery, and destructive live restoration remain unverified. Existing deprecation warnings and local unencrypted Qdrant-connection warnings did not fail the tests.
