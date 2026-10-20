"""Golden-set evaluation.

Retrieval (deterministic): recall@k and MRR against expected source documents.
Generation: faithfulness + answer relevancy, judged by the `eval_judge` route (a different vendor than generation).

  python evals/run_eval.py --tenant-id <uuid> [--provider anthropic|openai|ollama] [--retrieval-only]
                           [--min-recall 0.85 --min-faithfulness 0.90]   # CI gate: exit 1 on regression
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tenant-id", required=True, type=uuid.UUID)
    p.add_argument("--golden", default=str(HERE / "golden.jsonl"))
    p.add_argument("--k", type=int, default=8)
    p.add_argument("--provider", help="Pin the generation route to one provider")
    p.add_argument("--retrieval-only", action="store_true")
    p.add_argument("--min-recall", type=float)
    p.add_argument("--min-faithfulness", type=float)
    args = p.parse_args()
    if args.provider:
        os.environ["RAG_PIN_PROVIDER"] = args.provider

    from rag.evaluation.judge import judge
    from rag.graph.graph import build_context, build_graph
    from rag.retrieval.retriever import retrieve

    cases = [json.loads(line) for line in Path(args.golden).read_text().splitlines() if line.strip()]
    rows: list[dict[str, Any]] = []
    for case in cases:
        row: dict[str, Any] = {"question": case["question"]}
        expected = set(case.get("expected_sources", []))
        if expected:
            res = await retrieve(args.tenant_id, case["question"])
            sources = [c.source for c in res.chunks[: args.k]]
            row["recall"] = len(expected & set(sources)) / len(expected)
            row["mrr"] = next((1 / (i + 1) for i, s in enumerate(sources) if s in expected), 0.0)
        if not args.retrieval_only:
            t0 = time.perf_counter()
            state = await build_graph().ainvoke(
                {
                    "tenant_id": str(args.tenant_id),
                    "user_id": "eval",
                    "conversation_id": "eval",
                    "question": case["question"],
                    "history": [],
                    "filters": {},
                    "path": [],
                    "models_used": [],
                },
                config={"recursion_limit": 12},
            )
            row["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            row["path"] = state["path"]
            row["llm_calls"] = len(state["models_used"])
            row["fallbacks"] = sum(u.fallback_used for u in state["models_used"])
            if "needs_retrieval" in case:
                row["routing_correct"] = state["needs_retrieval"] == case["needs_retrieval"]
            if state.get("chunks") and "generate" in state["path"]:
                verdict, _, _ = await judge(build_context(state["chunks"]), case["question"], state["answer"])
                row["faithfulness"] = verdict.faithfulness
                row["relevancy"] = verdict.relevancy
        rows.append(row)
        print(json.dumps(row), file=sys.stderr)

    def mean(key: str) -> float | None:
        vals = [r[key] for r in rows if key in r]
        return round(statistics.mean(vals), 4) if vals else None

    summary = {
        "provider": args.provider or "route-default",
        "n": len(rows),
        f"recall@{args.k}": mean("recall"),
        "mrr": mean("mrr"),
        "faithfulness": mean("faithfulness"),
        "relevancy": mean("relevancy"),
        "routing_accuracy": mean("routing_correct"),
        "p95_latency_ms": sorted(r["latency_ms"] for r in rows)[int(0.95 * (len(rows) - 1))]
        if not args.retrieval_only
        else None,
        "max_llm_calls": max((r.get("llm_calls", 0) for r in rows), default=0),
        "fallback_rate": mean("fallbacks"),
    }
    out = HERE / "results" / f"{int(time.time())}-{summary['provider']}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    print(json.dumps(summary, indent=2))

    failed = (args.min_recall is not None and (summary[f"recall@{args.k}"] or 0) < args.min_recall) or (
        args.min_faithfulness is not None and (summary["faithfulness"] or 0) < args.min_faithfulness
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
