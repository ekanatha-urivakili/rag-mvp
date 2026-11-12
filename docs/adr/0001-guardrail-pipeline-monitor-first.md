# ADR-0001: Guardrails as a staged pipeline, monitor mode first

- Status: Proposed
- Date: 2026-11-12
- Deciders: LLM path owner, security owner

## Context

Safety checks exist but are scattered: escaping in `src/rag/retrieval/context.py:13`, an instruction in `src/rag/graph/prompts/generate.v1.txt`, citation validation in `src/rag/graph/graph.py:275`, a length cap in `ChatIn`. None can be measured, switched off, or trialled without blocking users. Heuristic guards have unknown false-positive rates on our traffic, and a false block on a grounded answer is a product failure.

## Decision

Introduce a `Guard` protocol and a per-stage pipeline (`admission`, `input`, `context`, `output_stream`, `output_final`) configured in `config/guardrails.yaml`. Each guard has a mode (`off`, `monitor`, `enforce`) and a failure policy (`fail_open`, `fail_closed`). Every new guard ships in `monitor`; promotion to `enforce` is a separate config PR backed by at least one week of verdict metrics and a review of flagged turns. Hot-path guards are deterministic and sub-millisecond; model-based checks run asynchronously.

## Options considered

| Option | For | Against |
| --- | --- | --- |
| Staged pipeline, monitor first (chosen) | Uniform telemetry; safe rollout; easy to reason about order | Small framework to maintain |
| Third-party guardrail library | Pattern libraries included | Adds a dependency in the hot path; opinions on prompt formats that conflict with our adapters; harder to keep text out of telemetry |
| LLM classifier on every turn | Higher recall on novel attacks | 0.3–1 s TTFT cost, its own failure mode, sends questions to another model |
| Keep ad hoc checks | No work | No measurement; every new check is a risky deploy |

## Consequences

- Guard verdicts become a first-class metric and part of `messages.debug`.
- A guard cannot block users on day one, so a known high-severity issue needs an explicit `enforce` from the start, justified in the PR.
- Guard order is configuration, so reviewers must check `config/guardrails.yaml` diffs as carefully as code.

## Revisit when

Labelled monitor data shows heuristic recall below 80% on a red-team set, which would justify an async or small-model classifier stage.
