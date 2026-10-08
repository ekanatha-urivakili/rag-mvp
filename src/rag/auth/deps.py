import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from rag.auth.rbac import Permission, Role
from rag.auth.tokens import decode_access_token, hash_token, parse_api_key_prefix, tokens_equal
from rag.core.errors import Forbidden, Unauthorized
from rag.db.models import ApiKey, Membership, RefreshToken, User
from rag.db.session import get_db
from rag.domain.models import RequestContext

_bearer = HTTPBearer(auto_error=False)
_LAST_USED_RESOLUTION = timedelta(minutes=1)

DB = Annotated[AsyncSession, Depends(get_db)]


def client_ip(request: Request) -> str | None:
    # Trust only what uvicorn resolved (--proxy-headers + --forwarded-allow-ips); never raw X-Forwarded-For.
    return request.client.host if request.client else None


async def _context_from_api_key(db: AsyncSession, raw: str, ip: str | None) -> RequestContext:
    prefix = parse_api_key_prefix(raw)
    if prefix is None:
        raise Unauthorized()
    key = (await db.execute(select(ApiKey).where(ApiKey.key_prefix == prefix))).scalar_one_or_none()
    if key is None or key.revoked_at is not None or not tokens_equal(key.key_hash, hash_token(raw)):
        raise Unauthorized("Invalid API key")
    now = datetime.now(UTC)
    # Coarse usage tracking: one write per key per minute instead of a write + commit on every request.
    if key.last_used_at is None or now - key.last_used_at > _LAST_USED_RESOLUTION:
        await db.execute(update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=now))
        await db.commit()
    return RequestContext(user_id=None, tenant_id=key.tenant_id, role=Role(key.role), api_key_id=key.id, ip=ip)


async def _context_from_jwt(db: AsyncSession, token: str, ip: str | None) -> RequestContext:
    claims = decode_access_token(token)
    user_id, tenant_id = uuid.UUID(claims["sub"]), uuid.UUID(claims["tid"])
    session_id = uuid.UUID(claims["sid"])
    session_live = (
        exists()
        .where(
            RefreshToken.family_id == session_id,
            RefreshToken.user_id == user_id,
            RefreshToken.tenant_id == tenant_id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > func.now(),
        )
        .label("session_live")
    )
    # Role is read from the DB on every request (never trusted from the token) so revocation is immediate.
    # One round-trip: membership, account state and session liveness together.
    row = (
        await db.execute(
            select(Membership.role, User.is_active, User.tokens_valid_after, session_live)
            .join(User, User.id == Membership.user_id)
            .where(Membership.user_id == user_id, Membership.tenant_id == tenant_id)
        )
    ).one_or_none()
    if row is None or not row.is_active:
        raise Unauthorized()
    issued_at = datetime.fromtimestamp(claims["iat"], UTC)
    if issued_at < row.tokens_valid_after:
        raise Unauthorized("Token revoked")
    if not row.session_live:
        raise Unauthorized("Session revoked")
    return RequestContext(user_id=user_id, tenant_id=tenant_id, role=Role(row.role), session_id=session_id, ip=ip)


async def get_context(
    request: Request,
    db: DB,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> RequestContext:
    ip = client_ip(request)
    if creds is not None and creds.scheme.lower() == "bearer":
        raw = creds.credentials
    elif api_key := request.headers.get("x-api-key"):
        raw = api_key
    else:
        raise Unauthorized()
    if raw.startswith("rk_"):
        return await _context_from_api_key(db, raw, ip)
    return await _context_from_jwt(db, raw, ip)


def require(permission: Permission, *, user_only: bool = False) -> Callable[..., Awaitable[RequestContext]]:
    async def _dep(ctx: Annotated[RequestContext, Depends(get_context)]) -> RequestContext:
        if not ctx.can(permission):
            raise Forbidden()
        if user_only and ctx.user_id is None:
            raise Forbidden("This endpoint requires a user session")
        return ctx

    _dep.__rag_permission__ = permission  # type: ignore[attr-defined]  # read by the RBAC route-coverage test
    return _dep


async def require_user(ctx: Annotated[RequestContext, Depends(get_context)]) -> RequestContext:
    """Authenticated human user (not an API key)."""
    if ctx.user_id is None:
        raise Forbidden("This endpoint requires a user session")
    return ctx


Ctx = Annotated[RequestContext, Depends(get_context)]
UserCtx = Annotated[RequestContext, Depends(require_user)]
