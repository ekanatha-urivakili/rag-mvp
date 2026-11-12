# ADR-0004: Hold-back window for streamed output instead of buffering or retracting

- Status: Proposed
- Date: 2026-11-12
- Deciders: LLM path owner, security owner, web owner

## Context

`_stream_answer` (`src/rag/graph/graph.py:170`) sends each model delta to the client as a `token` event. Citation validation runs after generation and sends a canonical `answer` event that replaces the provisional text. Any output check placed after generation therefore runs after the user has seen the text. TTFT is an SLO (p95 ≤ 3 s), so the fix cannot be "wait for the whole answer".

## Decision

Pass deltas through a hold-back buffer that releases text only once it is at least `HOLD_BACK` (default 64) characters behind the stream head and the window covering it has passed the synchronous stream guards. Redactions apply to the buffer, so stored text equals displayed text. A block cancels generation and sends a safe `answer`. Checks that cannot run in under 1 ms per window stay in `output_final` or the async judge. The canonical `answer` event remains authoritative.

## Options considered

| Option | For | Against |
| --- | --- | --- |
| Hold-back window (chosen) | Guards see text before the user does; TTFT cost < 100 ms | Only pattern-like checks fit; semantic issues still surface after release |
| Buffer the full answer | Any check possible | TTFT becomes total generation time (seconds); breaks the SLO |
| Stream, then retract (status quo) | Lowest latency | The user has already seen and may have copied the text; unacceptable for secrets |
| Sentence-level release | Natural unit for semantic checks | Variable and sometimes long delay; still too slow for model-based checks |

## Consequences

- The web client sees slightly burstier tokens; no protocol change.
- The `token` event contract becomes "guard-approved text", which the web UI and API clients can rely on.
- A guard that needs more context than the window must declare `output_final` and accept post-release semantics.

## Revisit when

A model-based output check becomes necessary on the hot path, which would force a choice between a larger hold-back and sentence-level release.
