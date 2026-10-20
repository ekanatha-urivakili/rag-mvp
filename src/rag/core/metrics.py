"""Prometheus metrics (§5.3). Process-local counters/histograms plus Postgres-derived gauges refreshed at
scrape time, so job and online-eval health come from the source of truth whichever replica is scraped."""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 13, 21, 34, 60, 120)

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency (streams: until headers)", ["method", "route"]
)

LLM_REQUESTS = Counter(
    "llm_requests_total", "LLM calls per provider attempt", ["purpose", "provider", "model", "outcome"]
)
LLM_LATENCY = Histogram(
    "llm_request_duration_seconds", "LLM call latency per attempt", ["purpose", "provider"], buckets=_LATENCY_BUCKETS
)
LLM_TOKENS = Counter("llm_tokens_total", "LLM tokens", ["purpose", "provider", "model", "direction"])
LLM_FALLBACK = Counter("llm_fallback_total", "Route fell through to the next provider", ["purpose", "from", "to"])

# outcome: answered | direct | insufficient_context | error
ANSWERS = Counter("rag_answers_total", "Chat turns by outcome", ["outcome"])
TTFT = Histogram(
    "rag_time_to_first_token_seconds", "Chat request start to first streamed text", buckets=_LATENCY_BUCKETS
)
ANSWER_DURATION = Histogram("rag_answer_duration_seconds", "Full chat turn duration", buckets=_LATENCY_BUCKETS)
TOP_SCORE = Histogram(
    "rag_top_rerank_score", "Top reranker score per retrieval", buckets=(-8, -4, -2, -1, 0, 1, 2, 4, 8)
)
RETRIEVAL_DEGRADED = Counter("rag_retrieval_degraded_total", "Retrieval fell back to a weaker mode", ["kind"])
FEEDBACK = Counter("rag_feedback_total", "Thumbs up/down submissions", ["rating"])

JOBS_QUEUED = Gauge("jobs_queued", "Queued jobs (backlog)", ["type"])
JOBS_OLDEST_QUEUED = Gauge("jobs_oldest_queued_age_seconds", "Age of the oldest runnable queued job", ["type"])
JOBS_FAILED_24H = Gauge("jobs_failed_24h", "Jobs that reached terminal failure in the last 24h", ["type"])
ONLINE_FAITHFULNESS = Gauge("rag_online_faithfulness_avg_24h", "Mean judged faithfulness of sampled answers, 24h")
ONLINE_RELEVANCY = Gauge("rag_online_relevancy_avg_24h", "Mean judged relevancy of sampled answers, 24h")
ONLINE_JUDGED = Gauge("rag_online_judged_24h", "Answers judged in the last 24h")


async def _refresh_db_gauges(db: AsyncSession) -> None:
    for gauge in (JOBS_QUEUED, JOBS_OLDEST_QUEUED, JOBS_FAILED_24H):
        gauge.clear()
    rows = await db.execute(
        text(
            "SELECT type, count(*) AS n, "
            "coalesce(extract(epoch FROM now() - min(run_after) FILTER (WHERE run_after <= now())), 0) AS age "
            "FROM jobs WHERE status = 'queued' GROUP BY type"
        )
    )
    for r in rows:
        JOBS_QUEUED.labels(r.type).set(r.n)
        JOBS_OLDEST_QUEUED.labels(r.type).set(float(r.age))
    rows = await db.execute(
        text(
            "SELECT type, count(*) AS n FROM jobs "
            "WHERE status = 'failed' AND created_at > now() - interval '24 hours' GROUP BY type"
        )
    )
    for r in rows:
        JOBS_FAILED_24H.labels(r.type).set(r.n)
    ev = (
        await db.execute(
            text(
                "SELECT count(*) AS n, avg(faithfulness) AS f, avg(relevancy) AS r FROM answer_evals "
                "WHERE created_at > now() - interval '24 hours'"
            )
        )
    ).one()
    ONLINE_JUDGED.set(ev.n)
    ONLINE_FAITHFULNESS.set(float(ev.f) if ev.f is not None else float("nan"))
    ONLINE_RELEVANCY.set(float(ev.r) if ev.r is not None else float("nan"))


async def render(db: AsyncSession) -> tuple[bytes, str]:
    await _refresh_db_gauges(db)
    return generate_latest(), CONTENT_TYPE_LATEST
