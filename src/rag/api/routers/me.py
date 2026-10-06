from fastapi import APIRouter
from sqlalchemy import select

from rag.api.schemas import MeOut, TenantOut
from rag.auth.deps import DB, Ctx
from rag.auth.rbac import ROLE_PERMISSIONS, Role
from rag.core.config import get_settings
from rag.db.models import Membership, Tenant, User

router = APIRouter(tags=["me"])


@router.get("/v1/me", response_model=MeOut)
async def me(ctx: Ctx, db: DB) -> MeOut:
    tenant = await db.get(Tenant, ctx.tenant_id)
    assert tenant is not None
    email = None
    tenants: list[TenantOut] = []
    if ctx.user_id is not None:
        user = await db.get(User, ctx.user_id)
        email = user.email if user else None
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
        tenant=TenantOut(id=tenant.id, name=tenant.name, role=ctx.role),
        permissions=sorted(p.value for p in ROLE_PERMISSIONS[ctx.role]),
        tenants=tenants,
        debug_enabled=get_settings().env == "dev",
    )
