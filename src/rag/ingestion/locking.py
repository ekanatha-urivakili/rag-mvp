from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def lock_index(db: AsyncSession, *, exclusive: bool = False) -> None:
    sql = "SELECT pg_advisory_xact_lock(7241901)" if exclusive else "SELECT pg_advisory_xact_lock_shared(7241901)"
    await db.execute(text(sql))
