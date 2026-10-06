import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select

from rag.api.schemas import InvitationOut, InviteIn, MemberOut, RoleIn
from rag.auth import service
from rag.auth.deps import DB, require
from rag.auth.rbac import Permission, Role
from rag.db.models import EmailToken, Membership, User
from rag.domain.models import RequestContext

router = APIRouter(tags=["members"])
Admin = Annotated[RequestContext, Depends(require(Permission.MEMBER_MANAGE, user_only=True))]


@router.get("/v1/members", response_model=list[MemberOut])
async def list_members(ctx: Admin, db: DB) -> list[MemberOut]:
    rows = (
        await db.execute(
            select(User.id, User.email, User.is_active, Membership.role, Membership.created_at)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.tenant_id == ctx.tenant_id)
            .order_by(User.email)
        )
    ).all()
    return [
        MemberOut(user_id=r.id, email=r.email, role=Role(r.role), is_active=r.is_active, joined_at=r.created_at)
        for r in rows
    ]


@router.patch("/v1/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def change_role(user_id: uuid.UUID, body: RoleIn, ctx: Admin, db: DB) -> None:
    await service.change_role(db, ctx, user_id, body.role)


@router.delete("/v1/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(user_id: uuid.UUID, ctx: Admin, db: DB) -> None:
    await service.remove_member(db, ctx, user_id)


@router.post("/v1/invitations", status_code=status.HTTP_202_ACCEPTED)
async def invite(body: InviteIn, ctx: Admin, db: DB) -> dict[str, str]:
    await service.invite(db, ctx, email=body.email, role=body.role)
    return {"status": "invited"}


@router.get("/v1/invitations", response_model=list[InvitationOut])
async def pending_invitations(ctx: Admin, db: DB) -> list[InvitationOut]:
    rows = (
        await db.execute(
            select(EmailToken)
            .where(
                EmailToken.tenant_id == ctx.tenant_id,
                EmailToken.type == "invite",
                EmailToken.used_at.is_(None),
                EmailToken.expires_at > datetime.now(UTC),
            )
            .order_by(EmailToken.created_at.desc())
        )
    ).scalars()
    return [
        InvitationOut(
            id=t.id, email=t.email, role=Role(t.role or "viewer"), expires_at=t.expires_at, created_at=t.created_at
        )
        for t in rows
    ]
