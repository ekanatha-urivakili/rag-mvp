from fastapi import APIRouter
from sqlalchemy import select

from rag.api.schemas import MeOut, ProfileIn, TenantOut
from rag.auth.deps import DB, Ctx, UserCtx
from rag.auth.rbac import ROLE_PERMISSIONS, Role
from rag.core.config import get_settings
from rag.db.models import Membership, Tenant, User

router = APIRouter(tags=["me"])


@router.get("/v1/me", response_model=MeOut)
async def me(ctx: Ctx, db: DB) -> MeOut:
    tenant = await db.get(Tenant, ctx.tenant_id)
    assert tenant is not None
    email = None
    name = None
    tenants: list[TenantOut] = []
    if ctx.user_id is not None:
        user = await db.get(User, ctx.user_id)
        email = user.email if user else None
        name = user.name if user else None
        rows = (
            await db.execute(
                select(Tenant.id, Tenant.name, Membership.role)
                .join(Membership, Membership.tenant_id == Tenant.id)
                .where(Membership.user_id == ctx.user_id)
                .order_by(Tenant.name)
            )
        ).all()
        tenants = [TenantOut(id=r.id, name=r.name, role=Role(r.role)) for r in rows]
    return MeOut(
        user_id=ctx.user_id,
        email=email,
        name=name,
        tenant=TenantOut(id=tenant.id, name=tenant.name, role=ctx.role),
        permissions=sorted(p.value for p in ROLE_PERMISSIONS[ctx.role]),
        tenants=tenants,
        debug_enabled=get_settings().env == "dev",
    )


@router.patch("/v1/me", status_code=204)
async def update_profile(body: ProfileIn, ctx: UserCtx, db: DB) -> None:
    user = await db.get(User, ctx.user_id)
    assert user is not None
    user.name = body.name
    await db.commit()
