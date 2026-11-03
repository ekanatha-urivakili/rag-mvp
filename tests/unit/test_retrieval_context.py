import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from rag.core.config import Settings
from rag.domain.models import ScoredChunk
from rag.evaluation.retrieval import retrieval_metrics
from rag.ingestion.chunker import count_tokens
from rag.retrieval import context
from rag.retrieval.context import build_context, select_context
from rag.retrieval.retriever import RetrievalResult


def chunk(index: int, text: str = "Evidence.", doc_id: str = "doc") -> ScoredChunk:
    return ScoredChunk(
        point_id=str(index),
        doc_id=doc_id,
        doc_version=1,
        chunk_index=index,
        text=text,
        heading_path="Guide",
        source="guide.md",
        page=index,
        score=1.0,
    )


def test_budget_counts_escaped_markup_and_preserves_whole_evidence() -> None:
    first = chunk(0, "<&> " * 30)
    second = chunk(1, "Different evidence.")
    budget = count_tokens(build_context([first]))
    assert select_context([first, second], budget) == [first]
    assert count_tokens(build_context(select_context([first, second], budget))) <= budget
    assert select_context([first], budget - 1) == []
    assert first.text == "<&> " * 30


def test_compression_skips_duplicates_and_oversized_chunks_without_losing_later_evidence() -> None:
    first = chunk(0)
    duplicate = chunk(1)
    different_doc = chunk(0, doc_id="other")
    oversized = chunk(2, "large " * 2000)
    last = chunk(3, "Last fact.")
    assert select_context([first, duplicate, oversized, different_doc, last], 500) == [first, different_doc, last]
    assert select_context([chunk(0, " \n ")], 500) == []


async def test_disabled_expansion_does_not_open_database(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    monkeypatch.setattr(context, "get_sessionmaker", session)
    assert await context.neighboring_chunks(uuid.uuid4(), [chunk(0)], 0) == []
    assert await context.neighboring_chunks(uuid.uuid4(), [], 1) == []
    session.assert_not_called()


def test_source_metrics_count_irrelevant_chunks_and_do_not_inflate_recall() -> None:
    metrics = retrieval_metrics(["noise", "a", "a", "b"], {"a", "b"}, 3)
    assert metrics.recall == 0.5
    assert metrics.precision == pytest.approx(2 / 3)
    assert metrics.mrr == 0.5
    empty = retrieval_metrics([], {"a"}, 8)
    assert empty.recall == empty.precision == empty.mrr == 0


def test_source_metrics_reject_unlabelled_cases_and_invalid_k() -> None:
    with pytest.raises(ValueError, match="expected source"):
        retrieval_metrics([], set(), 8)
    with pytest.raises(ValueError, match="positive"):
        retrieval_metrics([], {"a"}, 0)


@pytest.mark.parametrize(
    "values",
    [
        {"context_max_tokens": 0},
        {"retrieval_top_k": 0},
        {"retrieval_prefetch": 0},
        {"retrieval_neighbor_radius": -1},
        {"retrieval_neighbor_radius": 3},
    ],
)
def test_context_settings_have_bounds(values: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        Settings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("expected", "threshold", "exit_code"), [(["guide.md"], 0.4, 0), (["guide.md"], 0.6, 1), ([], 0.0, 1)]
)
async def test_eval_precision_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expected: list[str],
    threshold: float,
    exit_code: int,
) -> None:
    from evals import run_eval

    from rag.retrieval import retriever

    golden = tmp_path / "golden.jsonl"
    golden.write_text(json.dumps({"question": "A fact?", "expected_sources": expected}))
    good = chunk(0)
    irrelevant = chunk(1).model_copy(update={"source": "noise.md"})
    monkeypatch.setattr(retriever, "retrieve", AsyncMock(return_value=RetrievalResult([good, irrelevant], True)))
    monkeypatch.setattr(run_eval, "HERE", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval",
            "--tenant-id",
            str(uuid.uuid4()),
            "--golden",
            str(golden),
            "--retrieval-only",
            "--min-precision",
            str(threshold),
        ],
    )
    assert await run_eval.main() == exit_code
    report = json.loads(next((tmp_path / "results").glob("*.json")).read_text())
    assert report["summary"]["precision@8"] == (0.5 if expected else None)
    assert report["summary"]["recall@8"] == (1.0 if expected else None)
