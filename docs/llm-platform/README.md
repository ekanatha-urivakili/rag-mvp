# LLM platform: state, caching, guardrails, observability

This folder sets the technical direction for the LLM request path in Moontig. It starts from a code-level assessment of what exists today (2026-11-12, `origin/main` at `bd4d462`), then defines the target architecture and the order in which to build it.

| Document | Purpose |
| --- | --- |
| [HLD.md](HLD.md) | Target architecture: the chat turn as a pipeline of state, cache, guard and telemetry stages |
| [LLD-01-state.md](LLD-01-state.md) | Turn lifecycle, idempotency, per-conversation single-flight, token-budgeted history |
| [LLD-02-caching.md](LLD-02-caching.md) | Layered caches, corpus-version invalidation, cache store interface |
| [LLD-03-guardrails.md](LLD-03-guardrails.md) | Guard pipeline (input, context, output, cost), streaming guards, monitor-then-enforce rollout |
| [LLD-04-observability.md](LLD-04-observability.md) | Cost accounting, SLOs and burn-rate alerts, cache and guard telemetry, feedback loop |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | PR-sized phases with acceptance criteria, flags and rollback |
| [../adr/](../adr/) | Decisions behind this design, one file per decision |
| [../engineering/TECH_DIRECTION.md](../engineering/TECH_DIRECTION.md) | Principles and review checklist for anyone changing the LLM path |

## Assessment

Scored against what a production multi-tenant LLM web system needs, not against the MVP scope in [ARCHITECTURE.md](../ARCHITECTURE.md).

| Area | Rating | Present today | Missing |
| --- | --- | --- | --- |
| **Observability** | Strong | OTel span per graph node and per LLM attempt with a privacy-filtering exporter (`src/rag/core/telemetry.py:41`); Prometheus LLM/TTFT/fallback/token metrics (`src/rag/core/metrics.py:15`); DB-derived gauges correct across replicas; alert rules each mapped to a runbook; sampled async faithfulness judge; per-message provider/model/tokens/latency/trace id (`src/rag/db/models.py:285`) | Cost in currency; SLOs and error budgets (alerts are static thresholds); cache and guard telemetry; per-tenant usage reporting (the Usage page shows document counts only); thumbs-down to eval-set loop; per-prompt versions (one global `PROMPT_VERSION`, `src/rag/graph/graph.py:29`) |
| **State** | Adequate | Stateless graph with a typed `RAGState` (`src/rag/graph/graph.py:40`); history reloaded from Postgres; rolling summary off the hot path (`src/rag/graph/summary.py`); partial answer saved on disconnect with a shielded write (`src/rag/api/routers/chat.py:173`); bounded loops (`recursion_limit: 12`, one rewrite) | No idempotency on `POST /v1/chat` (a retried request creates a second turn); no per-conversation single-flight (two tabs interleave history); no turn status, so a client that reconnects cannot tell "still running" from "lost"; history window is 6 turns × 1500 chars, not a token budget |
| **Guardrails** | Partial | Retrieved text escaped and labelled as untrusted (`src/rag/retrieval/context.py:13`, `src/rag/graph/prompts/generate.v1.txt`); citation validation replaces uncited answers (`src/rag/graph/graph.py:275`); Pydantic validation of structured output; input length cap; per-tenant and per-user request rate limits; no side-effecting tools; log redaction; PAN masking for receipts | No guard abstraction, so checks are scattered and cannot run in monitor mode; no prompt-injection signal on questions or on ingested chunks; no output scan for secrets, PII or system-prompt leakage; **provisional tokens reach the client before any output check** (`src/rag/graph/graph.py:170`); no token or cost budget (request-count limits only, while a user can select `max` reasoning effort) |
| **Caching** | Weak | Anthropic `cache_control` on the static system prompt (`src/rag/adapters/llm/anthropic.py:55`); process-local `lru_cache` for clients and models | Cached-token counts are dropped (`Usage` has only `tokens_in`/`tokens_out`, `src/rag/adapters/llm/base.py:41`), so prompt-cache value is unmeasured; no query-embedding, retrieval or answer cache; no invalidation primitive tied to corpus changes |
| **Technical direction** | Partial | Detailed [ARCHITECTURE.md](../ARCHITECTURE.md) with revision history and an ADR summary table; [RUNBOOKS.md](../RUNBOOKS.md); [RAG_QUALITY.md](../RAG_QUALITY.md) with an eval-gated rollout method; strict CI | ADRs live in one table without status, context or consequences; no RFC process; no written review checklist for LLM changes, which is where most regressions come from |

**Verdict.** The project already demonstrates observability and RAG-quality discipline at a senior level. The gaps that a principal reviewer would flag first are: the output path streams unchecked model text, there is no cost control, chat turns are not idempotent, and caching does not exist beyond one provider feature. This design closes those gaps without adding infrastructure until a metric requires it.

## Design principles

1. **Measure before optimising.** Cost and cache-token telemetry ship before any cache. A cache whose hit rate nobody can see is a correctness risk with no proven benefit.
2. **Postgres stays the source of truth.** Caches are derived and disposable. Invalidation is a version bump in the same transaction as the change, never a scan.
3. **Every guard ships in monitor mode first.** A guard emits a verdict metric for at least a week of real traffic before it can block.
4. **No new infrastructure without a trigger.** Redis is a documented scale step with a measurable threshold ([ADR-0002](../adr/0002-cache-store-postgres-first.md)), not a starting point.
5. **Tenant scope is part of every key.** Cache keys, budget counters and guard state all include `tenant_id`. Cross-tenant reuse is never an optimisation.
