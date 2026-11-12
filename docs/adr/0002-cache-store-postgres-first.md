# ADR-0002: Cache store on Postgres first, Redis on a measured trigger

- Status: Proposed
- Date: 2026-11-12
- Deciders: LLM path owner, platform owner

## Context

Retrieval and answer caches need a store shared across API replicas. The platform deliberately runs without Redis: jobs, outbox and rate limits all live in Postgres ([ARCHITECTURE.md §9](../ARCHITECTURE.md)). Expected cache traffic is one lookup and at most one write per chat turn, bounded by the chat rate limits (120 per tenant per minute by default).

## Decision

Define a two-method `CacheStore` interface. Implement it with a Postgres `UNLOGGED` table keyed by `(namespace, sha256)` with an `expires_at` column and periodic purge. Store errors are misses. Move to Redis behind the same interface only when a trigger below fires.

## Options considered

| Option | For | Against |
| --- | --- | --- |
| Postgres UNLOGGED (chosen) | No new service; transactional neighbour of the data it caches; no WAL cost; truncated on crash, which is correct for a cache | Shares connection pool and I/O with primary workload; no native TTL |
| Redis now | Sub-millisecond lookups; native TTL and eviction | New service to run, secure, back up and monitor, before any hit-rate data exists |
| In-process LRU only | Zero infrastructure | Hit rate divided by replica count; no sharing between API replicas |

## Consequences

- Cache lookups add one indexed Postgres read per turn (~1–3 ms).
- The table must be excluded from logical backups (add it to the exclusions in `scripts/backup.py`).
- Moving to Redis later is a new `CacheStore` implementation plus a setting; callers do not change.

## Revisit when

Any of: p95 `rag_cache_lookup_seconds` > 10 ms; cache writes > 200/s sustained; the cache table exceeds 5 GB; or Redis is introduced for another reason (queue, BFF refresh lock per [ARCHITECTURE.md §7.3](../ARCHITECTURE.md)).
