from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from rag.api.schemas import AuditOut
from rag.auth.deps import DB, require
from rag.auth.rbac import Permission
from rag.db.models import AuditLog
from rag.domain.models import RequestContext

router = APIRouter(tags=["audit"])


@router.get("/v1/audit-log", response_model=list[AuditOut])
async def audit_log(
    ctx: Annotated[RequestContext, Depends(require(Permission.AUDIT_READ))],
    db: DB,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_id: Annotated[int | None, Query(ge=1)] = None,
) -> list[AuditOut]:
    q = select(AuditLog).where(AuditLog.tenant_id == ctx.tenant_id)
    if before_id is not None:
        q = q.where(AuditLog.id < before_id)
    rows = (await db.execute(q.order_by(AuditLog.id.desc()).limit(limit))).scalars()
    return [
        AuditOut(
            id=r.id,
            actor_user_id=r.actor_user_id,
            action=r.action,
            target_type=r.target_type,
            target_id=r.target_id,
            metadata=r.metadata_,
            ip=str(r.ip) if r.ip else None,
            created_at=r.created_at,
        )
        for r in rows
    ]
