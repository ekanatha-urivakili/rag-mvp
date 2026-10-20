# Runbooks

Operational procedures for the Moontig RAG stack ([architecture](ARCHITECTURE.md), §8 Phase 6). Commands assume the local Podman deployment from the repo root; in production, run the same scripts against the production `.env` from a host that can reach Postgres, Qdrant and object storage.

Alerts in [`ops/prometheus/alerts.yml`](../ops/prometheus/alerts.yml) point here:

| Alert | Runbook |
| --- | --- |
| `LlmFallbackRateHigh`, `LlmRouteExhausted`, `ChatErrorsHigh`, `ChatTimeToFirstTokenSlow` | [Provider outage](#provider-outage) |
| `JobsFailing`, `JobBacklogStale` | [Job failures and backlog](#job-failures-and-backlog) |
| `InsufficientContextRateHigh`, `ThumbsDownRateHigh`, `OnlineFaithfulnessLow` | [Answer quality regression](#answer-quality-regression) |
| `ApiDown`, `ApiErrorRateHigh` | [API down or erroring](#api-down-or-erroring) |
| Data loss, bad migration, corrupted index | [Restore](#restore) |
| Embedding model change, corrupted or missing index | [Re-indexing](#re-indexing) |

Useful views: Grafana dashboard **Moontig RAG overview** (`podman compose --profile observability up -d`, then `http://127.0.0.1:3001`), Prometheus alerts at `http://127.0.0.1:9090/alerts`, traces in Jaeger at `http://127.0.0.1:16686` (or Langfuse). Every chat message stores its `trace_id`, `provider`, `model` and `fallback_used`, and every JSON log line carries `request_id` and `trace_id`.

---

## Provider outage

**Symptoms:** fallback rate per route rising, `llm_fallback_total{to="none"}` increasing (every provider failed), SSE `error` events with `llm_unavailable`, TTFT p95 above 3 s.

1. Find which provider is failing:
   ```promql
   sum by (purpose, provider, outcome) (rate(llm_requests_total[5m]))
   sum by (purpose, from, to) (rate(llm_fallback_total[5m]))
   ```
   Then check the API logs: `podman compose logs api | grep llm_fallback` (each line has `purpose`, `provider`, `model`, `to`, `error`).
2. **Ollama (local, orchestration route):**
   - `curl -s http://127.0.0.1:11434/api/version`; `/readyz` reports `ollama: degraded` while it is down.
   - Restart it on the host (`ollama serve`, or `./start.sh`, which also pulls and warms models). A cold model load can exceed the 30 s orchestration timeout; the API warms models at startup, so restart the API after Ollama if the first calls keep timing out.
   - Under load, `OLLAMA_NUM_PARALLEL` limits concurrent requests; queued requests show up as rising `llm_request_duration_seconds` for `provider="ollama"`.
3. **Anthropic or OpenAI:** check the provider's status page. Errors with `outcome="error"` and a `401`/`403` in the logs mean an invalid or revoked key: rotate `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` and restart `api` and `worker`. `429` means the account rate limit was reached.
4. **Mitigate with routing (config only, no code change):** reorder or remove the failing entry for the route in `config/models.yaml` and restart `api` and `worker`. Examples: put `anthropic/claude-haiku-4-5` first for `orchestration` while Ollama is down; drop a failing cloud generation provider so requests skip its timeout. Providers without credentials are skipped automatically.
5. **Verify:** fallback rate returns under 5%, `rag_answers_total{outcome="error"}` stops increasing, and a test chat shows the expected model in the chat header. Revert temporary routing changes after the provider recovers, then run the eval suite if a different model now serves a route (`evals/run_eval.py`).

## Job failures and backlog

**Symptoms:** `jobs_failed_24h` > 0, `jobs_oldest_queued_age_seconds` above 10 min, documents stuck in `queued`/`processing`.

1. Check the worker is running: `podman compose ps worker`, `podman compose logs --tail 200 worker`. A stale backlog with no `job_done` lines means the worker is down or stuck; restart it with `podman compose restart worker`. Jobs locked by a crashed worker are reclaimed after 15 minutes.
2. Inspect failures (error messages are redacted, never contain secrets):
   ```bash
   podman compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
     SELECT type, status, attempts, left(error, 200), created_at FROM jobs
     WHERE status = '\''failed'\'' AND created_at > now() - interval '\''1 day'\'' ORDER BY created_at DESC LIMIT 20"'
   ```
3. By type:
   - `ingest_document`: the document is marked `failed` and the uploader is emailed. Common causes are an unreachable embedding provider (see [Provider outage](#provider-outage)), unsupported content, or page/pixel limits. Re-uploading the same file queues a new attempt.
   - `send_email`: check SMTP settings and the provider (Mailpit locally: `http://127.0.0.1:8025`). Payloads are scrubbed after terminal failure, so the user must request the email again (reset password, resend invitation).
   - `reindex`: see [Re-indexing](#re-indexing); a retry rebuilds the incomplete target collection.
   - `judge_answer`: the `eval_judge` route is unreachable or misconfigured. Answers are unaffected; fix the provider or set `ONLINE_EVAL_SAMPLE_RATE=0`.
4. Backlog with a healthy worker means throughput is insufficient: add worker replicas (`SKIP LOCKED` makes this safe).

## Answer quality regression

**Symptoms:** `insufficient_context` share above 30%, thumbs-down share above 30%, online faithfulness below 0.90.

1. Check whether the change is real: low volume skews ratios. Look at `rag_answers_total` by outcome and `rag_top_rerank_score` on the dashboard.
2. A rising `insufficient_context` share with low top rerank scores: users ask about content that isn't uploaded, or `GRADE_THRESHOLD` is too strict. Tune the threshold with the eval suite, not by guessing.
3. Check for degraded retrieval: `rag_retrieval_degraded_total{kind="sparse"|"rerank"}` means BM25 or the reranker is failing and retrieval fell back.
4. Check for a model change: a fallback now serving generation (`llm_requests_total` by provider), or a recent change to `config/models.yaml` or prompts (`messages.prompt_version`).
5. Reproduce offline: `uv run python evals/run_eval.py --tenant-id <eval tenant> --provider <provider>` and compare with the last recorded results in `evals/results/`.

## API down or erroring

1. `curl -s http://127.0.0.1:8000/readyz` shows which dependency is down (Postgres, Qdrant, storage, Ollama).
2. `podman compose logs --tail 200 api`; 5xx responses log `unhandled_error` with a `request_id` and `trace_id`. Find the trace in Jaeger/Langfuse by trace ID.
3. Restart the failing dependency, then the API. Migrations run on API start locally; in production they run as a release step, so a failed migration blocks the release, not the running version.

---

## Restore

Backups cover Postgres (source of truth), the Qdrant collection behind the alias, and raw uploads in object storage. Qdrant can always be rebuilt from Postgres with [re-indexing](#re-indexing); its snapshot is only the faster path.

**Take a backup** (consistent Postgres snapshot; safe while the app is running):

```bash
uv run python scripts/backup.py backup                    # writes backups/<UTC timestamp>/
uv run python scripts/backup.py backup --skip-objects     # database + index only
```

**Restore test** (the Phase 6 exit check; run after every backup job and at least weekly). It restores into a scratch database and scratch Qdrant collection, compares row and point counts with the manifest, then removes both. It does not touch live data:

```bash
uv run python scripts/backup.py verify backups/<timestamp>
```

**Restore live data** (destructive; overwrites the database and index):

1. Stop traffic and background work: `podman compose stop web api worker`.
2. Restore: `uv run python scripts/backup.py restore backups/<timestamp> --yes`. Postgres restores in a single transaction (all or nothing). The Qdrant snapshot is recovered into its original collection and the alias is pointed at it. Raw files are uploaded; objects created after the backup are left in place and are unreferenced.
3. If the backup predates the current schema, start the API so `alembic upgrade head` runs, or run it as a release step in production.
4. Start the services: `podman compose up -d`. Check `/readyz` and the document list.
5. If the Qdrant snapshot is missing or bad, restore Postgres only and run [re-indexing](#re-indexing) to a new collection version.

Production notes: keep backups off the database host, encrypted, with retention that matches your recovery point objective. Managed Postgres point-in-time recovery and Qdrant Cloud snapshots replace the corresponding steps here; keep the restore test.

## Re-indexing

Rebuilds the Qdrant index from Postgres chunks (no re-parsing) into a new collection and atomically flips the alias. Use it to change the embedding model or dimension, or to recover a lost or corrupted index.

1. Pick the next version: `curl -s -H "api-key: $QDRANT_API_KEY" http://127.0.0.1:6333/aliases` shows the current target (e.g. `chunks_v1` → use `--version 2`).
2. For a model or dimension change, set `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL` and `EMBEDDING_DIM` for both `api` and `worker`, and pause query traffic: queries embedded with the new model must not hit the old collection.
3. Queue it: `podman compose exec api rag reindex --version 2`. The worker locks out ingestion, upload and delete while it runs; follow progress in `podman compose logs -f worker`.
4. When the job is `done`, restart `api` with the new settings and resume traffic. Spot-check a few questions and run the eval suite (recall@8 ≥ 0.85).
5. Rollback: point the alias back at the previous collection (it is kept) and restore the previous embedding settings. Delete retired collections after the rollback window.
