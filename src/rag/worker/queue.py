from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from rag.db.models import Job


def enqueue(db: AsyncSession, job_type: str, payload: dict[str, Any]) -> Job:
    """Adds a job to the caller's transaction — committed atomically with the business change (outbox)."""
    job = Job(type=job_type, payload=payload, status="queued", attempts=0)
    db.add(job)
    return job
