# RAG quality assessment

Reviewed against the 15 requested concepts on 2026-11-03. This is a code assessment and initial implementation phase, not a claim that retrieval quality has improved on a live corpus.

## Implementation map

| # | Concept | Current implementation and remaining work |
|---|---|---|
| 1 | Chunking | `src/rag/ingestion/chunker.py:75`: heading-aware paragraph packing, token splitting, overlap and page provenance. Evaluate chunk size against representative documents. |
| 2 | Embedding models | `src/rag/adapters/embedder.py:25`: OpenAI/Ollama dense embeddings and local BM25 sparse embeddings. Model/dimension changes require reindexing; compare models on a labelled corpus. |
| 3 | Vector database | `src/rag/adapters/vectorstore.py:35`: Qdrant named dense/sparse vectors, tenant payload indexes, deterministic point IDs and atomic alias changes. |
| 4 | Metadata filtering | `src/rag/adapters/vectorstore.py:49`: mandatory tenant filter and allowlisted document/category filters. `src/rag/retrieval/retriever.py:45` also verifies ready status, version and category in PostgreSQL. Date ranges and per-document ACLs remain absent; tenant-wide membership is the access boundary. |
| 5 | Query rewriting | `src/rag/graph/graph.py:129`: conversation-aware standalone query; `src/rag/graph/graph.py:199`: one corrective rewrite on poor retrieval. |
| 6 | HyDE | Added, off by default (`RETRIEVAL_HYDE`). `src/rag/graph/graph.py:210` writes a short hypothetical passage per retrieval attempt; `src/rag/retrieval/retriever.py:29` adds its embedding as one extra dense search alongside the real query. It is never sparse-searched, never a rerank query and never context, so it cannot be cited. Failure falls back to query-only search (`rag_retrieval_degraded_total{kind="hyde"}`). |
| 7 | Query decomposition | Added, off by default (`RETRIEVAL_MAX_SUBQUERIES`). The existing analysis call (`src/rag/graph/graph.py:137`) also returns up to N subqueries for multi-part questions; no extra model call. Fewer than two distinct subqueries means no decomposition. All queries share the request filters, one RRF fusion, one candidate pool, one context budget; each chunk keeps its best cross-encoder score across the queries (`src/rag/retrieval/retriever.py:91`). Subqueries are recorded in the chat debug trace. |
| 8 | Hybrid search | `src/rag/adapters/vectorstore.py:140`: dense + sparse prefetch and reciprocal rank fusion; dense-only fallback if sparse embedding fails. |
| 9 | Reranking | `src/rag/retrieval/retriever.py:73`: cross-encoder reranking over heading plus text, then selection; fallback to fusion ranking on failure. |
| 10 | Small-to-big retrieval | Added in `src/rag/retrieval/context.py:39`: optional adjacent chunks from the same document/version, tenant-scoped, ready-only, deduplicated and budgeted. Each neighbor retains its own page and citation. This is bounded adjacency expansion, not a parent-document hierarchy. |
| 11 | GraphRAG | Not implemented. LangGraph controls workflow; it does not supply an entity/relationship graph. Requires entity extraction, tenant-scoped storage, provenance, lifecycle cleanup and a multi-hop benchmark before adoption. |
| 12 | Context compression | Added in `src/rag/retrieval/context.py:25`: deterministic selection of whole chunks, exact duplicate removal and an escaped-context token budget. No lossy sentence extraction or LLM summarization. |
| 13 | Agentic RAG | `src/rag/graph/graph.py:274`: bounded analyze/retrieve/grade/rewrite/generate workflow. No unrestricted tool planning or side-effecting agent. |
| 14 | Faithfulness | `src/rag/evaluation/judge.py:35`: offline judge and opt-in sampled asynchronous checks. `src/rag/graph/graph.py:228` validates citation indices only; a valid citation does not prove a claim. |
| 15 | Context precision/recall | `src/rag/evaluation/retrieval.py:11` and `evals/run_eval.py:55`: deterministic source-label recall, precision and MRR, with an optional precision gate. Chunk relevance and evidence completeness still require finer annotations. |

## Findings and first implementation

1. **Medium, fixed:** retrieval previously limited chunk count without bounding rendered context. Long headings and XML escaping can increase prompt size. Selection now counts the actual escaped document blocks and removes exact duplicates within a document/version/heading before generation. It skips oversized chunks intact; it does not truncate evidence.
2. **Medium, fixed:** Qdrant deliberately permits category-less legacy vectors. PostgreSQL previously checked tenant, status and version but not requested category, allowing irrelevant legacy documents through receipt/document filters. The authoritative category is now checked before reranking.
3. **Medium, partially addressed:** evals reported source recall and MRR without precision. Precision and an optional threshold are now available; source labels still cannot distinguish an irrelevant passage from a relevant passage in the same document.
4. **Medium, open:** `src/rag/graph/graph.py:191` accepts nonempty retrieval without a relevance threshold when reranking is unavailable. Fusion scores and cross-encoder scores need separate calibration. A reranker failure can still produce irrelevant context; citation validation does not establish factual support.
5. **Evaluation limit, partially addressed:** faithfulness is judged only for generated answers with chunks, so declining more often could raise it. `answer_rate` now reports the share of labelled cases that produced a cited answer, with an optional `--min-answer-rate` gate. The golden set has no unanswerable cases yet, so a correct decline is not rewarded. It contains example source names and requires a matching seeded corpus.
6. **Medium, fixed:** `--retrieval-only` calls the retriever with the raw question, bypassing analysis, decomposition and HyDE. Full runs now also report `graph_recall@k` and `graph_precision@k` over the chunks the graph actually used, which is the metric to compare query strategies on.

## Settings and behavior

| Setting | Default | Bounds and effect |
|---|---|---|
| `CONTEXT_MAX_TOKENS` | `6000` | 128–32000. Counts escaped document blocks, headings, source labels and separators using `cl100k_base`. Excludes system prompt, question and answer allowance; other providers tokenize differently. Choose a value that leaves room for those fields in the selected model. |
| `RETRIEVAL_NEIGHBOR_RADIUS` | `0` | 0–2. Zero avoids the expansion query. One includes the preceding/following chunk for each selected hit; two includes up to two on either side. |
| `RETRIEVAL_MAX_SUBQUERIES` | `0` | 0–4. Zero sends the original analysis prompt and schema. Otherwise each decomposed question adds one dense and one sparse search and one rerank pass per subquery. |
| `RETRIEVAL_HYDE` | `false` | Adds one orchestration-route model call and one dense search per retrieval attempt, including the corrective retry. |
| `RETRIEVAL_TOP_K` | `8` | 1–40 seed hits before expansion. Neighbors can increase the final chunk count, within the shared token budget. |
| `RETRIEVAL_PREFETCH` | `40` | 1–200 candidates per retrieval prefetch. |

Ranked seed hits get first use of the budget. Neighbors follow in seed priority and increasing distance; their inherited score is not an independent relevance estimate. Overlapping windows do not repeat the same chunk. Expansion cannot include another tenant, a failed/deleted document or a superseded version. Selected chunks remain unchanged, so citation numbering and asynchronous judge reconstruction still refer to the evidence used for generation. No schema migration or reindex is required for these additions.

## Evaluation and rollout

Run the existing seeded golden set with the same model, corpus and settings for both arms of each experiment:

```sh
uv run python evals/run_eval.py --tenant-id <uuid> --retrieval-only --k 8 --min-recall 0.85
uv run python evals/run_eval.py --tenant-id <uuid> --min-recall 0.85 --min-faithfulness 0.90
```

The runner also accepts `--min-precision <0-to-1>`. Choose a threshold from a reviewed baseline; it is not enabled in CI by default. An explicitly requested gate fails if its metric has no eligible cases. Precision is relevant returned chunks divided by returned chunks (zero for no hits); a chunk is considered relevant when its source title appears in `expected_sources`. Recall is unique expected source titles retrieved divided by all expected source titles. Duplicate chunks cannot inflate recall, but multiple chunks from an expected source count as relevant for this precision proxy. Duplicate titles across documents are ambiguous. These are source-label proxies, not semantic context precision or claim-level context recall.

`--k` measures the first k returned chunks, including neighbors if enabled. Set it high enough to assess all supplied context when comparing expansion; also inspect final chunk counts, latency, answer coverage and manual evidence quality. Keep radius zero until an expansion experiment improves evidence completeness without unacceptable precision or latency loss.

Compare strategies one at a time against a baseline on the same corpus and models, using full runs:

```sh
uv run python evals/run_eval.py --tenant-id <uuid> --k 8                              # baseline
RETRIEVAL_MAX_SUBQUERIES=3 uv run python evals/run_eval.py --tenant-id <uuid> --k 8   # decomposition
RETRIEVAL_HYDE=true uv run python evals/run_eval.py --tenant-id <uuid> --k 8          # HyDE
```

Enable a strategy only if `graph_recall@k` or `answer_rate` improves without an unacceptable drop in `graph_precision@k`, faithfulness or p95 latency. Decomposition needs multi-part questions to show any effect; the current golden set has few. Next, label relevant chunk positions and expected evidence facts, and add multi-part, unanswerable, contradictory, stale-version and access-boundary cases. GraphRAG is justified only if these evaluations show a persistent relationship-retrieval gap.

## Primary references

- [HyDE paper](https://arxiv.org/abs/2212.10496): hypothetical document generation followed by retrieval of real evidence.
- [Ragas paper](https://arxiv.org/abs/2309.15217): separate evaluation dimensions for retrieval context and faithful answer generation. This project uses its own evaluator, not the Ragas package.
