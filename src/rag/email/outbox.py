from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from rag.worker.queue import enqueue

Template = Literal["invite", "password_reset", "ingestion_failed", "signup", "account_exists"]


def queue_email(db: AsyncSession, *, template: Template, to: str, context: dict[str, Any]) -> None:
    enqueue(db, "send_email", {"template": template, "to": to, "context": context})
