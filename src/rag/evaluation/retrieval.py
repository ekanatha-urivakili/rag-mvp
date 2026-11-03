from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalMetrics:
    recall: float
    precision: float
    mrr: float


def retrieval_metrics(sources: list[str], expected: set[str], k: int) -> RetrievalMetrics:
    """Source-label proxies: a chunk is relevant when its source is in the gold set."""
    if k < 1:
        raise ValueError("k must be positive")
    if not expected:
        raise ValueError("Retrieval metrics require at least one expected source")
    retrieved = sources[:k]
    return RetrievalMetrics(
        recall=len(expected.intersection(retrieved)) / len(expected),
        precision=sum(source in expected for source in retrieved) / len(retrieved) if retrieved else 0.0,
        mrr=next((1 / rank for rank, source in enumerate(retrieved, 1) if source in expected), 0.0),
    )
