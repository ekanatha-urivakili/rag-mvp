import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from rag.db.models import AuditLog


def audit(
    db: AsyncSession,
    *,
    action: str,
    tenant_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    ip: str | None = None,
    **metadata: Any,
) -> None:
    """Adds an audit row to the caller's transaction (committed with the business change)."""
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id else None,
            metadata_=metadata,
            ip=ip,
        )
    )
