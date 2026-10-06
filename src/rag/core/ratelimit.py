import time
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rag.core.errors import RateLimited

# Fixed-window counter in Postgres so limits hold across stateless API replicas (OWASP API4).
_SQL = text(
    """
    INSERT INTO rate_limits (key, window_start, count) VALUES (:key, :ws, 1)
    ON CONFLICT (key, window_start) DO UPDATE SET count = rate_limits.count + 1
    RETURNING count
    """
)


async def hit(db: AsyncSession, key: str, limit: int, window_s: int) -> None:
    now = int(time.time())
    start = now - now % window_s
    count = (await db.execute(_SQL, {"key": key[:200], "ws": datetime.fromtimestamp(start, UTC)})).scalar_one()
    await db.commit()
    if count > limit:
        raise RateLimited(retry_after=start + window_s - now)


async def purge_expired(db: AsyncSession, older_than_s: int = 86400) -> None:
    await db.execute(
        text("DELETE FROM rate_limits WHERE window_start < now() - make_interval(secs => :s)"),
        {"s": older_than_s},
    )
