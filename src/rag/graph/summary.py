"""Rolling conversation summary (§4.10): folds messages that slid out of the HISTORY_TURNS window into one summary.

Runs as the `summarize_conversation` worker job after an answer is saved, so it never adds latency to a chat turn.
"""

import logging
import uuid
from contextlib import nullcontext
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rag.adapters.llm.base import Message
from rag.adapters.llm.connections import resolve_chat_model
from rag.adapters.llm.router import get_router
from rag.auth.rbac import Role
from rag.core.config import get_settings
from rag.core.logging import log_extra
from rag.db.models import Conversation, Membership, User
from rag.db.models import Message as MessageRow
from rag.domain.models import RequestContext
from rag.worker.queue import enqueue

log = logging.getLogger(__name__)

SUMMARY_MAX_CHARS = 2000
_TURN_MAX_CHARS = 1500


async def message_count(db: AsyncSession, conversation_id: uuid.UUID) -> int:
    count: int = (
        await db.execute(
            select(func.count()).select_from(MessageRow).where(MessageRow.conversation_id == conversation_id)
        )
    ).scalar_one()
    return count


def maybe_enqueue_summary(db: AsyncSession, conversation_id: uuid.UUID, total: int, summarized: int) -> None:
    """Queue a summary update when messages have slid out of the window and are not summarised yet."""
    if total - get_settings().history_turns > summarized:
        enqueue(db, "summarize_conversation", {"conversation_id": str(conversation_id)})


async def summarize_conversation(db: AsyncSession, payload: dict[str, Any]) -> None:
    from rag.graph.graph import prompt

    conversation_id = uuid.UUID(payload["conversation_id"])
    conv = await db.get(Conversation, conversation_id)
    if conv is None:
        return  # deleted since
    role = await db.scalar(
        select(Membership.role)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.user_id == conv.user_id,
            Membership.tenant_id == conv.tenant_id,
            User.is_active.is_(True),
        )
    )
    if role is None:
        return
    selection = (
        await resolve_chat_model(
            db,
            RequestContext(user_id=conv.user_id, tenant_id=conv.tenant_id, role=Role(role)),
            conv.model_id,
        )
        if conv.model_id
        else None
    )
    done, previous = conv.summarized_count, conv.summary
    target = await message_count(db, conversation_id) - get_settings().history_turns
    if target <= done:
        return
    rows = (
        await db.execute(
            select(MessageRow.role, MessageRow.content)
            .where(MessageRow.conversation_id == conversation_id)
            .order_by(MessageRow.created_at, MessageRow.id)
            .offset(done)
            .limit(target - done)
        )
    ).all()
    await db.commit()  # no transaction or row lock held across the model call

    turns = "\n".join(f"{r.role.upper()}: {r.content[:_TURN_MAX_CHARS]}" for r in rows)
    user = (
        f"Current summary:\n<summary>\n{previous or '(empty)'}\n</summary>\n\n"
        f"Next messages:\n<messages>\n{turns}\n</messages>"
    )
    scope = get_router().select_chat(selection.entry, selection.api_key) if selection else nullcontext()
    async with scope:
        res = await (
            get_router()
            .for_purpose("orchestration")
            .complete([Message("system", prompt("summarize_conversation")), Message("user", user)], max_tokens=400)
        )
    summary = res.text.strip()[:SUMMARY_MAX_CHARS]
    if not summary:
        return

    conv = (
        await db.execute(select(Conversation).where(Conversation.id == conversation_id).with_for_update())
    ).scalar_one_or_none()
    if conv is None or conv.summarized_count != done:
        return  # deleted, or another job already advanced the summary
    conv.summary, conv.summarized_count = summary, target
    await db.commit()
    log.info("conversation_summarized", extra=log_extra(conversation_id=str(conversation_id), messages=target))
