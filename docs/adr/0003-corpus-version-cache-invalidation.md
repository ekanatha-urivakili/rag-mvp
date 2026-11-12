# ADR-0003: Cache invalidation by corpus version; no semantic answer cache

- Status: Proposed
- Date: 2026-11-12
- Deciders: LLM path owner

## Context

A cached retrieval or answer is wrong as soon as the tenant's documents change: a new version becomes ready, a document is deleted, or the index is rebuilt. Explicit deletion of affected cache entries requires knowing which queries touched which documents, and races with the transaction that changes the document. Separately, semantic caches (reuse an answer when a new question embeds close to an old one) are often proposed for LLM cost savings.

## Decision

1. Add `tenants.corpus_version`, incremented in the same transaction as any change to what retrieval can return. Include it in every retrieval and answer cache key. Old entries become unreachable and expire by TTL.
2. On every hit, re-validate the cached chunk references against Postgres (tenant, ready, current version), as retrieval already does. A failed check is a `stale` miss.
3. Do not build a semantic answer cache.

## Options considered

| Option | For | Against |
| --- | --- | --- |
| Corpus version in key (chosen) | Invalidation is atomic with the change; no scan; trivially correct across replicas | Any document change invalidates the whole tenant's cache |
| Per-document dependency tracking | Finer invalidation | Index of query→documents to maintain; race-prone; complexity disproportionate to cache value |
| TTL only | Simple | Serves deleted content until expiry: unacceptable for deletion requests |
| Semantic answer cache | Higher hit rate on paraphrases | A near-miss returns a confident answer to a different question, citations included; threshold tuning needs a labelled paraphrase set we do not have; the failure is silent |

## Consequences

- Tenants that upload continuously get low hit rates. That is acceptable: their answers must reflect new documents.
- A code path that changes document visibility without bumping the version is a correctness bug. The `stale` outcome metric and a test that enumerates status-changing paths are the guards against it.

## Revisit when

A labelled paraphrase set exists and shows a similarity threshold with ≥ 99% precision on "same answer" pairs; then a semantic retrieval cache (not answer cache) is the first candidate, since re-validation and generation still run after it.
