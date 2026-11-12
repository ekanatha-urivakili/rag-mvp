# Architecture decision records

One file per decision. The summary table in [ARCHITECTURE.md §9](../ARCHITECTURE.md) records decisions made before this folder existed; new decisions go here and are linked from that table.

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-guardrail-pipeline-monitor-first.md) | Guardrails as a staged pipeline, monitor mode first | Proposed |
| [0002](0002-cache-store-postgres-first.md) | Cache store on Postgres first, Redis on a measured trigger | Proposed |
| [0003](0003-corpus-version-cache-invalidation.md) | Cache invalidation by corpus version; no semantic answer cache | Proposed |
| [0004](0004-stream-hold-back-output-guard.md) | Hold-back window for streamed output instead of buffering or retracting | Proposed |

## When to write one

Write an ADR when a change is hard to reverse, adds infrastructure, changes a data contract (schema, SSE events, cache keys, `RAGState`), or rejects an option a reasonable engineer would propose. If the PR description would need a "why not X" paragraph, that paragraph is an ADR.

## Template

```markdown
# ADR-NNNN: <decision as a short statement>

- Status: Proposed | Accepted | Superseded by ADR-MMMM
- Date: YYYY-MM-DD
- Deciders: <roles>

## Context
What forces the decision: constraints, measurements, incidents. Link code as `path:line`.

## Decision
What we will do, in one paragraph.

## Options considered
| Option | For | Against |

## Consequences
What becomes easier, what becomes harder, what we must now monitor.

## Revisit when
The measurable condition that would reopen this decision.
```

Status moves to Accepted when the implementing PR merges. A superseded ADR is kept and linked, never deleted.
