import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert

from rag.api.schemas import (
    ChatIn,
    ConversationDetailOut,
    ConversationOut,
    ConversationPatchIn,
    FeedbackIn,
    MessageOut,
)
from rag.auth.deps import DB, require
from rag.auth.rbac import Permission
from rag.core import ratelimit
from rag.core.config import get_settings
from rag.core.errors import LLMUnavailable, NotFound
from rag.core.logging import log_extra, request_id_var
from rag.db.models import Conversation, Feedback, Message
from rag.db.session import get_sessionmaker
from rag.domain.models import ChatTurn, RequestContext
from rag.graph.graph import PROMPT_VERSION, RAGState, build_graph

log = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])
ChatUser = Annotated[RequestContext, Depends(require(Permission.CHAT_USE, user_only=True))]


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _own_conversation(db: DB, ctx: RequestContext, conversation_id: uuid.UUID) -> Conversation:
    # Own conversations only — admins cannot read other users' chats (BOLA guard).
    conv = (
        await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == ctx.tenant_id,
                Conversation.user_id == ctx.user_id,
            )
        )
    ).scalar_one_or_none()
    if conv is None:
        raise NotFound("Conversation not found")
    return conv


@router.post("/v1/chat")
async def chat(body: ChatIn, ctx: ChatUser, db: DB) -> StreamingResponse:
    s = get_settings()
    await ratelimit.hit(db, f"chat:tenant:{ctx.tenant_id}", s.rl_chat_per_tenant, s.rl_chat_window_s)
    await ratelimit.hit(db, f"chat:user:{ctx.user_id}", s.rl_chat_per_user, s.rl_chat_window_s)
    assert ctx.user_id is not None

    if body.conversation_id:
        # Continuing an existing chat: it moves to the top of the history list.
        conv = await _own_conversation(db, ctx, body.conversation_id)
        await db.execute(update(Conversation).where(Conversation.id == conv.id).values(updated_at=func.now()))
    else:
        conv = Conversation(
            id=uuid.uuid4(), tenant_id=ctx.tenant_id, user_id=ctx.user_id, title=body.message[:80].replace("\n", " ")
        )
        db.add(conv)
        await db.flush()

    recent = (
        await db.execute(
            select(Message.role, Message.content)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.created_at.desc())
            .limit(s.history_turns)
        )
    ).all()
    history = [ChatTurn(role=r.role, content=r.content) for r in reversed(recent)]
    db.add(Message(conversation_id=conv.id, role="user", content=body.message))
    await db.commit()

    filters = {"doc_id": str(body.filters.doc_id)} if body.filters and body.filters.doc_id else None
    state: RAGState = {
        "tenant_id": str(ctx.tenant_id),
        "user_id": str(ctx.user_id),
        "conversation_id": str(conv.id),
        "question": body.message,
        "history": history,
        "filters": filters or {},
        "path": [],
        "models_used": [],
    }
    trace_id = request_id_var.get() or uuid.uuid4().hex
    return StreamingResponse(
        _run(state, conv.id, trace_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def _run(state: RAGState, conversation_id: uuid.UUID, trace_id: str) -> AsyncIterator[str]:
    t0 = time.perf_counter()
    final: dict[str, Any] = {}
    try:
        async for mode, chunk in build_graph().astream(
            state, stream_mode=["custom", "values"], config={"recursion_limit": 12}
        ):
            if mode == "values":
                assert isinstance(chunk, dict)
                final = chunk
                continue
            assert isinstance(chunk, dict)
            kind = chunk.get("type")
            if kind == "status":
                yield _sse("status", {"step": chunk["step"]})
            elif kind == "model":
                yield _sse("model", {k: chunk[k] for k in ("purpose", "provider", "model")})
            elif kind == "token":
                yield _sse("token", {"text": chunk["text"]})
            elif kind == "answer":
                yield _sse("answer", {"text": chunk["text"]})
            elif kind == "citations":
                yield _sse("citations", chunk["citations"])
    except LLMUnavailable:
        log.exception("chat_llm_unavailable")
        yield _sse("error", {"code": "llm_unavailable", "message": "The language model is unavailable. Try again."})
        return
    except Exception:
        log.exception("chat_failed")
        yield _sse("error", {"code": "internal_error", "message": "Something went wrong.", "trace_id": trace_id})
        return

    usages = final.get("models_used", [])
    answer_usage = usages[-1] if usages else None
    debug = {
        "path": final.get("path", []),
        "standalone_query": final.get("standalone_query"),
        "scores": [round(c.score, 4) for c in final.get("chunks", [])],
        "models": [u.model_dump() for u in usages],
    }
    async with get_sessionmaker()() as db:
        msg = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=final.get("answer", ""),
            citations=[c.model_dump() for c in final.get("citations", [])],
            trace_id=trace_id,
            prompt_version=PROMPT_VERSION,
            provider=answer_usage.provider if answer_usage else None,
            model=answer_usage.model if answer_usage else None,
            fallback_used=any(u.fallback_used for u in usages),
            tokens_in=sum(u.tokens_in for u in usages),
            tokens_out=sum(u.tokens_out for u in usages),
            latency_ms=int((time.perf_counter() - t0) * 1000),
            debug=debug,
        )
        db.add(msg)
        await db.execute(update(Conversation).where(Conversation.id == conversation_id).values(updated_at=func.now()))
        await db.commit()
    log.info("chat_done", extra=log_extra(trace_id=trace_id, path=debug["path"], latency_ms=msg.latency_ms))
    if get_settings().env == "dev":
        yield _sse("debug", debug)
    yield _sse("done", {"message_id": str(msg.id), "conversation_id": str(conversation_id)})


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/v1/conversations", response_model=list[ConversationOut])
async def list_conversations(
    ctx: ChatUser,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    before: Annotated[datetime | None, Query(description="Keyset cursor: updated_at of the last item seen")] = None,
    before_id: Annotated[uuid.UUID | None, Query(description="Keyset cursor tie-breaker: id of the last item")] = None,
) -> list[Conversation]:
    """Own conversations, most recently active first. Page with the last item's (updated_at, id)."""
    stmt = select(Conversation).where(Conversation.tenant_id == ctx.tenant_id, Conversation.user_id == ctx.user_id)
    if q:
        stmt = stmt.where(Conversation.title.ilike(f"%{_escape_like(q)}%", escape="\\"))
    if before is not None:
        if before_id is not None:
            stmt = stmt.where(tuple_(Conversation.updated_at, Conversation.id) < tuple_(before, before_id))
        else:
            stmt = stmt.where(Conversation.updated_at < before)
    stmt = stmt.order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars())


@router.get("/v1/conversations/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation(
    conversation_id: uuid.UUID,
    ctx: ChatUser,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    before: Annotated[datetime | None, Query(description="Only messages older than this (load earlier)")] = None,
) -> ConversationDetailOut:
    conv = await _own_conversation(db, ctx, conversation_id)
    stmt = select(Message).where(Message.conversation_id == conv.id)
    if before is not None:
        stmt = stmt.where(Message.created_at < before)
    newest_first = list((await db.execute(stmt.order_by(Message.created_at.desc()).limit(limit + 1))).scalars())
    return ConversationDetailOut(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=[MessageOut.model_validate(m) for m in reversed(newest_first[:limit])],
        has_more=len(newest_first) > limit,
    )


@router.patch("/v1/conversations/{conversation_id}", response_model=ConversationOut)
async def rename_conversation(
    conversation_id: uuid.UUID, body: ConversationPatchIn, ctx: ChatUser, db: DB
) -> Conversation:
    conv = await _own_conversation(db, ctx, conversation_id)
    conv.title = body.title.replace("\n", " ")
    await db.commit()
    await db.refresh(conv)
    return conv


@router.delete("/v1/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: uuid.UUID, ctx: ChatUser, db: DB) -> None:
    # Hard delete: messages and feedback cascade. Chat content is user data, not an audit record.
    conv = await _own_conversation(db, ctx, conversation_id)
    await db.delete(conv)
    await db.commit()


@router.post("/v1/messages/{message_id}/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def feedback(message_id: uuid.UUID, body: FeedbackIn, ctx: ChatUser, db: DB) -> None:
    owned = (
        await db.execute(
            select(Message.id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.id == message_id,
                Message.role == "assistant",
                Conversation.tenant_id == ctx.tenant_id,
                Conversation.user_id == ctx.user_id,
            )
        )
    ).scalar_one_or_none()
    if owned is None:
        raise NotFound("Message not found")
    stmt = insert(Feedback).values(message_id=message_id, user_id=ctx.user_id, rating=body.rating, comment=body.comment)
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=[Feedback.message_id, Feedback.user_id],
            set_={"rating": body.rating, "comment": body.comment},
        )
    )
    await db.commit()
