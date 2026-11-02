import uuid

from fastapi import APIRouter, Response, status
from sqlalchemy import func, select

from rag.adapters.llm.connections import connection_out, encrypt_key, personal_models, server_models
from rag.api.routers.chat import ChatUser
from rag.api.schemas import ChatModelOut, ChatModelsOut, ModelConnectionIn
from rag.auth.deps import DB
from rag.core import ratelimit
from rag.core.errors import AppError, NotFound
from rag.db.models import ModelConnection, User

router = APIRouter(tags=["models"])


@router.get("/v1/chat-models", response_model=ChatModelsOut)
async def list_models(ctx: ChatUser, db: DB) -> ChatModelsOut:
    items = server_models() + [connection_out(row) for row in await personal_models(db, ctx)]
    return ChatModelsOut(items=items, default_id=items[0].id if items else None)


@router.post("/v1/model-connections", response_model=ChatModelOut, status_code=status.HTTP_201_CREATED)
async def add_model(body: ModelConnectionIn, ctx: ChatUser, db: DB) -> ChatModelOut:
    await ratelimit.hit(db, f"models:user:{ctx.user_id}", 20, 60)
    await db.scalar(select(User).where(User.id == ctx.user_id).with_for_update())
    count = await db.scalar(
        select(func.count())
        .select_from(ModelConnection)
        .where(
            ModelConnection.user_id == ctx.user_id,
            ModelConnection.tenant_id == ctx.tenant_id,
        )
    )
    if count is not None and count >= 20:
        raise AppError("You can save up to 20 personal models per workspace. Remove an unused model first.")
    row = ModelConnection(
        id=uuid.uuid4(),
        user_id=ctx.user_id,
        tenant_id=ctx.tenant_id,
        provider=body.provider,
        model=body.model,
    )
    if body.api_key is not None:
        row.encrypted_api_key = encrypt_key(row, body.api_key)
    db.add(row)
    await db.commit()
    return connection_out(row)


@router.delete("/v1/model-connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_model(connection_id: uuid.UUID, ctx: ChatUser, db: DB) -> Response:
    row = await db.scalar(
        select(ModelConnection).where(
            ModelConnection.id == connection_id,
            ModelConnection.user_id == ctx.user_id,
            ModelConnection.tenant_id == ctx.tenant_id,
        )
    )
    if row is None:
        raise NotFound("Model connection not found")
    await db.delete(row)
    await db.commit()
    return Response(status_code=204)
