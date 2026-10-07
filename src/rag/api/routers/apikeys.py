import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select

from rag.api.schemas import ApiKeyCreatedOut, ApiKeyIn, ApiKeyOut
from rag.auth.deps import DB, require
from rag.auth.rbac import Permission
from rag.auth.tokens import new_api_key
from rag.core.audit import audit
from rag.core.errors import NotFound
from rag.db.models import ApiKey
from rag.domain.models import RequestContext

router = APIRouter(prefix="/v1/api-keys", tags=["api-keys"])
Admin = Annotated[RequestContext, Depends(require(Permission.APIKEY_MANAGE, user_only=True))]


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(
    ctx: Admin,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> list[ApiKey]:
    q = (
        select(ApiKey)
        .where(ApiKey.tenant_id == ctx.tenant_id)
        .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(q)).scalars())


@router.post("", response_model=ApiKeyCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_key(body: ApiKeyIn, ctx: Admin, db: DB) -> ApiKeyCreatedOut:
    plaintext, prefix, key_hash = new_api_key()
    key = ApiKey(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        name=body.name,
        role=body.role.value,
        key_prefix=prefix,
        key_hash=key_hash,
        created_by=ctx.user_id,
    )
    db.add(key)
    audit(
        db,
        action="apikey.created",
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        target_type="api_key",
        target_id=key.id,
        ip=ctx.ip,
        role=body.role.value,
    )
    await db.commit()
    await db.refresh(key)
    return ApiKeyCreatedOut.model_validate({**ApiKeyOut.model_validate(key).model_dump(), "api_key": plaintext})


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(key_id: uuid.UUID, ctx: Admin, db: DB) -> None:
    key = (
        await db.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == ctx.tenant_id))
    ).scalar_one_or_none()
    if key is None:
        raise NotFound()
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        audit(
            db,
            action="apikey.revoked",
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            target_type="api_key",
            target_id=key.id,
            ip=ctx.ip,
        )
        await db.commit()
