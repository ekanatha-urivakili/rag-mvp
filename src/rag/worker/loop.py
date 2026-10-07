import asyncio
import contextlib
import logging
import random
import signal
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rag.adapters.storage import get_storage
from rag.adapters.vectorstore import get_vectorstore
from rag.core import ratelimit
from rag.core.config import get_settings
from rag.core.logging import configure_logging, log_extra, redact
from rag.db.session import get_sessionmaker
from rag.email.handler import send_email
from rag.ingestion.pipeline import ingest_document, on_ingest_failed, purge_document
from rag.ingestion.reindex import reindex

log = logging.getLogger("rag.worker")

Handler = Callable[[AsyncSession, str, dict[str, Any]], Awaitable[None]]
FailureHook = Callable[[AsyncSession, dict[str, Any], str], Awaitable[None]]

HANDLERS: dict[str, Handler] = {
    "ingest_document": lambda db, _id, p: ingest_document(db, p),
    "send_email": lambda _db, job_id, p: send_email(job_id, p),
    "reindex": lambda db, _id, p: reindex(db, p),
    "purge_document": lambda db, _id, p: purge_document(db, p),
}
ON_FAILURE: dict[str, FailureHook] = {"ingest_document": on_ingest_failed}

_CLAIM = text(
    """
    UPDATE jobs SET status = 'running', locked_at = now(), attempts = attempts + 1
    WHERE id = (
        SELECT id FROM jobs
        WHERE (status = 'queued' AND run_after <= now())
           OR (status = 'running' AND locked_at < now() - interval '15 minutes')
        ORDER BY run_after
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id, type, payload, attempts
    """
)


async def _finish(db: AsyncSession, job_id: str, job_type: str) -> None:
    scrub = ", payload = jsonb_build_object('redacted', true)" if job_type == "send_email" else ""
    await db.execute(text(f"UPDATE jobs SET status = 'done', error = NULL{scrub} WHERE id = :id"), {"id": job_id})  # noqa: S608
    await db.commit()


async def _fail(
    db: AsyncSession, job_id: str, job_type: str, payload: dict[str, Any], attempts: int, err: BaseException
) -> None:
    message = redact(f"{type(err).__name__}: {err}")[:1000]
    if attempts >= get_settings().job_max_attempts:
        scrub = ", payload = jsonb_build_object('redacted', true)" if job_type == "send_email" else ""
        await db.execute(
            text(f"UPDATE jobs SET status = 'failed', error = :e{scrub} WHERE id = :id"),  # noqa: S608
            {"id": job_id, "e": message},
        )
        if hook := ON_FAILURE.get(job_type):
            async with get_sessionmaker()() as business_db:
                await hook(business_db, payload, message)
    else:
        delay = 10 * 2 ** (attempts - 1) + random.uniform(0, 5)  # noqa: S311
        await db.execute(
            text(
                "UPDATE jobs SET status = 'queued', error = :e, "
                "run_after = now() + make_interval(secs => :d) WHERE id = :id"
            ),
            {"id": job_id, "e": message, "d": delay},
        )
    await db.commit()


async def run_once() -> bool:
    async with get_sessionmaker()() as lease_db:
        row = (await lease_db.execute(_CLAIM)).one_or_none()
        if row is None:
            return False
        job_id, job_type, payload, attempts = str(row.id), row.type, row.payload, row.attempts
        started = time.perf_counter()
        # Persist the claim while retaining a separate row lock during processing.
        await lease_db.commit()
        owned = (
            await lease_db.execute(
                text(
                    "SELECT id FROM jobs WHERE id = :id AND status = 'running' AND attempts = :attempts "
                    "FOR UPDATE SKIP LOCKED"
                ),
                {"id": job_id, "attempts": attempts},
            )
        ).scalar_one_or_none()
        if owned is None:
            return True
        try:
            if attempts > get_settings().job_max_attempts:
                raise RuntimeError("Job exceeded the retry limit after worker interruption")
            async with get_sessionmaker()() as db:
                await HANDLERS[job_type](db, job_id, payload)
            await _finish(lease_db, job_id, job_type)
            log.info(
                "job_done",
                extra=log_extra(job_id=job_id, type=job_type, ms=int((time.perf_counter() - started) * 1000)),
            )
        except Exception as e:
            log.exception("job_failed", extra=log_extra(job_id=job_id, type=job_type, attempts=attempts))
            await _fail(lease_db, job_id, job_type, payload, attempts, e)
    return True


async def main() -> None:
    s = get_settings()
    configure_logging(s.log_level)
    await get_vectorstore().ensure_collection()
    await get_storage().ensure_bucket()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    log.info("worker_started")
    last_purge = 0.0
    while not stop.is_set():
        if time.monotonic() - last_purge > 3600:
            async with get_sessionmaker()() as db:
                await ratelimit.purge_expired(db)
                await db.commit()
            last_purge = time.monotonic()
        if not await run_once():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=s.worker_poll_interval_s)
    log.info("worker_stopped")
