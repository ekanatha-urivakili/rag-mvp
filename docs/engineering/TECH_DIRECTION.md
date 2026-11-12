# Technical direction: changing the LLM path

For engineers changing prompts, graph nodes, retrieval, model routing, caches or guards. Most production regressions in LLM systems come from changes that pass unit tests and still make answers worse, slower or more expensive. The rules below exist to catch those.

## Principles

1. **An eval run is the test for behaviour.** Unit tests prove the code does what you wrote; only the golden set shows whether answers improved. Any change to `graph/`, `retrieval/`, `graph/prompts/`, `config/models.yaml` or `config/prompts.yaml` includes baseline and change numbers from `evals/run_eval.py` in the PR description. Method: [RAG_QUALITY.md](../RAG_QUALITY.md#evaluation-and-rollout).
2. **Measure, then protect, then optimise.** A cache, model swap or prompt trim needs a metric that shows the before and after. If the metric does not exist, adding it is the first PR.
3. **New behaviour ships off or in monitor mode.** Flags for retrieval strategies and caches; `monitor` for guards ([ADR-0001](../adr/0001-guardrail-pipeline-monitor-first.md)). Turning it on is a separate, small PR with data attached.
4. **Postgres decides visibility.** Qdrant, caches and provider prompt caches are derived. Anything served to a user is re-checked against tenant, status and document version in Postgres.
5. **No user or document text in telemetry.** Spans, metrics and logs carry ids, enums, counts and scores. New span attributes go through `_SAFE_ATTRIBUTES` and `test_trace_privacy.py`.
6. **Bound everything.** Loops, retries, tokens, history, context, stream duration, cache size, cost. Every new loop or accumulation states its bound in code.
7. **Prefer deterministic checks on the hot path.** Model calls go on the hot path only when nothing deterministic works, and then with a timeout, a fallback and a metric.

## Review checklist for LLM-path PRs

Copy into the PR description and tick or explain.

- [ ] **Eval**: baseline vs change on the golden set (recall, precision, faithfulness, answer rate, p95 latency), same corpus and models.
- [ ] **Cost**: change in tokens per turn and in model calls per turn, by route.
- [ ] **Latency**: effect on TTFT; load test if a hot-path step was added.
- [ ] **Tenancy**: every new query, cache key and counter includes `tenant_id`; cross-tenant test added.
- [ ] **Failure**: what happens when the new dependency or model call fails; metric for the degraded path.
- [ ] **Prompt cache**: system prompts remain static; volatile content stays in user messages.
- [ ] **Telemetry**: new metrics have low-cardinality labels; new span attributes are allowlisted and privacy-tested.
- [ ] **Flags**: new behaviour defaults to current behaviour; rollback is a config change.
- [ ] **Docs**: ARCHITECTURE.md, RUNBOOKS.md or an ADR updated if a contract, alert or decision changed.

## RFCs

Write a short RFC (an issue or `docs/rfcs/NNNN-title.md`) before code when a change spans more than one of: API contract, schema, graph topology, a new provider or service, or the web client. Sections: problem with evidence, proposal, alternatives, rollout and rollback, metrics that will show success. Reviewed within three working days; silence is not approval. Decisions that come out of an RFC are recorded as ADRs.

## How we grow engineers on this codebase

- **Start with telemetry work.** A first PR that adds a metric or a dashboard row teaches the request path end to end with low blast radius. Phase 1 of the [implementation plan](../llm-platform/IMPLEMENTATION_PLAN.md) is deliberately sized for this.
- **Design review before code for anything on the hot path.** Thirty minutes with a diagram saves a week of rework; the LLDs in [`llm-platform/`](../llm-platform/) are the expected level of detail.
- **Rotate the weekly eval-candidate review.** Reading real failure cases (by trace id) builds intuition for retrieval and grounding faster than any document.
- **Authors present their change.** Five minutes in the weekly architecture review: what changed, what the numbers showed, what surprised them.
- **Reviewers explain the why.** A review comment that cites a principle or ADR teaches; "change this" does not.
