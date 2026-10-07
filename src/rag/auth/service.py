import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from rag.auth.passwords import hash_password, validate_password_policy, verify_password
from rag.auth.rbac import Role
from rag.auth.tokens import create_access_token, hash_token, new_opaque_token
from rag.core import ratelimit
from rag.core.audit import audit
from rag.core.config import get_settings
from rag.core.errors import AppError, Conflict, Forbidden, NotFound, Unauthorized
from rag.db.models import EmailToken, Membership, RefreshToken, Tenant, User
from rag.domain.models import RequestContext
from rag.email.outbox import queue_email


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    tenant_id: uuid.UUID


def _now() -> datetime:
    return datetime.now(UTC)


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def _issue_tokens(
    db: AsyncSession, user_id: uuid.UUID, tenant_id: uuid.UUID, family_id: uuid.UUID | None = None
) -> tuple[TokenPair, RefreshToken]:
    raw = new_opaque_token()
    rt = RefreshToken(
        id=uuid.uuid4(),
        user_id=user_id,
        tenant_id=tenant_id,
        family_id=family_id or uuid.uuid4(),
        token_hash=hash_token(raw),
        expires_at=_now() + timedelta(seconds=get_settings().refresh_token_ttl_s),
    )
    db.add(rt)
    access, ttl = create_access_token(user_id, tenant_id, rt.family_id)
    return TokenPair(access, raw, ttl, tenant_id), rt


async def _pick_membership(db: AsyncSession, user_id: uuid.UUID, tenant_id: uuid.UUID | None) -> Membership:
    q = select(Membership).where(Membership.user_id == user_id)
    if tenant_id is not None:
        q = q.where(Membership.tenant_id == tenant_id)
    m = (await db.execute(q.order_by(Membership.created_at).limit(1))).scalar_one_or_none()
    if m is None:
        raise Unauthorized("Invalid credentials")
    return m


async def login(
    db: AsyncSession, *, email: str, password: str, tenant_id: uuid.UUID | None, ip: str | None
) -> TokenPair:
    s = get_settings()
    email = normalize_email(email)
    await ratelimit.hit(db, f"login:ip:{ip}", s.rl_login_per_ip, s.rl_login_window_s)
    await ratelimit.hit(db, f"login:email:{email}", s.rl_login_per_email, s.rl_login_window_s)

    user = (await db.execute(select(User).where(User.email == email).with_for_update())).scalar_one_or_none()
    ok, upgraded_hash = await asyncio.to_thread(verify_password, password, user.password_hash if user else None)
    if user is None or not ok or not user.is_active:
        audit(db, action="auth.login_failed", tenant_id=None, actor_user_id=user.id if user else None, ip=ip)
        await db.commit()
        raise Unauthorized("Invalid credentials")  # identical message for every failure (no enumeration)

    membership = await _pick_membership(db, user.id, tenant_id)
    if upgraded_hash:
        user.password_hash = upgraded_hash
    user.last_login_at = _now()
    pair, _ = await _issue_tokens(db, user.id, membership.tenant_id)
    await db.commit()
    return pair


async def refresh(db: AsyncSession, raw: str) -> TokenPair:
    user_id = (
        await db.execute(select(RefreshToken.user_id).where(RefreshToken.token_hash == hash_token(raw)))
    ).scalar_one_or_none()
    if user_id is None:
        raise Unauthorized("Invalid refresh token")
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
    rt = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)).with_for_update())
    ).scalar_one_or_none()
    if rt is None:
        raise Unauthorized("Invalid refresh token")
    if rt.revoked_at is not None:
        # A rotated token was replayed: assume theft and kill the whole family.
        await _revoke_family(db, rt.family_id)
        audit(db, action="auth.refresh_reuse_detected", tenant_id=rt.tenant_id, actor_user_id=rt.user_id)
        await db.commit()
        raise Unauthorized("Invalid refresh token")
    if rt.expires_at <= _now():
        raise Unauthorized("Refresh token expired")

    active = (
        await db.execute(
            select(User.is_active)
            .join(Membership, Membership.user_id == User.id)
            .where(User.id == rt.user_id, Membership.tenant_id == rt.tenant_id)
        )
    ).scalar_one_or_none()
    if not active:
        await _revoke_family(db, rt.family_id)
        await db.commit()
        raise Unauthorized("Invalid refresh token")

    pair, new_rt = await _issue_tokens(db, rt.user_id, rt.tenant_id, rt.family_id)
    rt.revoked_at = _now()
    rt.replaced_by = new_rt.id
    await db.commit()
    return pair


async def _revoke_family(db: AsyncSession, family_id: uuid.UUID) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


async def _revoke_all_for_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


async def logout(db: AsyncSession, ctx: RequestContext, raw: str | None) -> None:
    if ctx.session_id is not None:
        await _revoke_family(db, ctx.session_id)
    if raw:
        rt = (
            await db.execute(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)))
        ).scalar_one_or_none()
        # Only the token's owner can revoke it.
        if rt is not None and rt.user_id == ctx.user_id:
            await _revoke_family(db, rt.family_id)
    await db.commit()


async def switch_tenant(db: AsyncSession, ctx: RequestContext, tenant_id: uuid.UUID) -> TokenPair:
    assert ctx.user_id is not None
    membership = (
        await db.execute(select(Membership).where(Membership.user_id == ctx.user_id, Membership.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if membership is None:
        raise Forbidden()
    pair, _ = await _issue_tokens(db, ctx.user_id, tenant_id)
    await db.commit()
    return pair


# --- Signup --------------------------------------------------------------------


async def signup(db: AsyncSession, *, email: str, ip: str | None) -> None:
    s = get_settings()
    if not s.signup_enabled:
        raise Forbidden("Signup is disabled. Ask your workspace admin for an invitation.")
    email = normalize_email(email)
    await ratelimit.hit(db, f"signup:ip:{ip}", s.rl_signup_per_ip, s.rl_signup_window_s)
    await ratelimit.hit(db, f"signup:email:{email}", 3, s.rl_signup_window_s)
    if (await db.execute(select(User.id).where(User.email == email))).scalar_one_or_none() is not None:
        # Same API response as a new email (no enumeration), but the owner learns why no signup link came.
        queue_email(db, template="account_exists", to=email, context={"login_link": f"{s.public_ui_url}/login"})
        await db.commit()
        return
    raw = new_opaque_token()
    db.add(
        EmailToken(
            type="signup",
            email=email,
            token_hash=hash_token(raw),
            expires_at=_now() + timedelta(seconds=s.signup_ttl_s),
        )
    )
    queue_email(
        db,
        template="signup",
        to=email,
        context={
            "link": f"{s.public_ui_url}/verify_signup?token={raw}",
            "expires_minutes": s.signup_ttl_s // 60,
        },
    )
    await db.commit()


async def verify_signup(
    db: AsyncSession, *, token: str, password: str, workspace_name: str, ip: str | None
) -> TokenPair:
    s = get_settings()
    if not s.signup_enabled:
        raise Forbidden("Signup is disabled. Ask your workspace admin for an invitation.")
    await ratelimit.hit(db, f"signup-verify:ip:{ip}", 20, s.rl_signup_window_s)
    tok = await _consume_email_token(db, token, "signup")
    validate_password_policy(password, tok.email)
    name = workspace_name.strip()
    if not name:
        raise AppError("Workspace name is required", code="invalid_workspace")
    user = User(id=uuid.uuid4(), email=tok.email, password_hash=await asyncio.to_thread(hash_password, password))
    tenant = Tenant(id=uuid.uuid4(), name=f"{name} ({user.id.hex[:12]})")
    db.add_all([user, tenant])
    try:
        await db.flush()
        db.add(Membership(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN.value))
        audit(db, action="auth.signup", tenant_id=tenant.id, actor_user_id=user.id, ip=ip)
        pair, _ = await _issue_tokens(db, user.id, tenant.id)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict("This account already exists. Sign in or reset your password.", code="account_exists") from exc
    return pair


# --- Password reset ------------------------------------------------------------


async def forgot_password(db: AsyncSession, *, email: str, ip: str | None) -> None:
    s = get_settings()
    email = normalize_email(email)
    await ratelimit.hit(db, f"forgot:ip:{ip}", s.rl_forgot_per_ip, s.rl_forgot_window_s)
    await ratelimit.hit(db, f"forgot:email:{email}", 3, s.rl_forgot_window_s)
    user = (await db.execute(select(User).where(User.email == email).with_for_update())).scalar_one_or_none()
    if user is None or not user.is_active:
        return
    raw = new_opaque_token()
    db.add(
        EmailToken(
            type="password_reset",
            email=email,
            token_hash=hash_token(raw),
            expires_at=_now() + timedelta(seconds=s.password_reset_ttl_s),
        )
    )
    queue_email(
        db,
        template="password_reset",
        to=email,
        context={
            "link": f"{s.public_ui_url}/reset_password?token={raw}",
            "expires_minutes": s.password_reset_ttl_s // 60,
        },
    )
    await db.commit()


async def _consume_email_token(db: AsyncSession, raw: str, token_type: str) -> EmailToken:
    tok = (
        await db.execute(
            select(EmailToken)
            .where(EmailToken.token_hash == hash_token(raw), EmailToken.type == token_type)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if tok is None or tok.used_at is not None or tok.expires_at <= _now():
        raise AppError("This link is invalid or has expired", code="invalid_token")
    tok.used_at = _now()
    return tok


async def reset_password(db: AsyncSession, *, token: str, new_password: str, ip: str | None) -> None:
    await ratelimit.hit(db, f"reset:ip:{ip}", 20, 3600)
    email = (
        await db.execute(
            select(EmailToken.email).where(
                EmailToken.token_hash == hash_token(token), EmailToken.type == "password_reset"
            )
        )
    ).scalar_one_or_none()
    if email is None:
        raise AppError("This link is invalid or has expired", code="invalid_token")
    user = (await db.execute(select(User).where(User.email == email).with_for_update())).scalar_one_or_none()
    await _consume_email_token(db, token, "password_reset")
    if user is None:
        raise AppError("This link is invalid or has expired", code="invalid_token")
    validate_password_policy(new_password, user.email)
    user.password_hash = await asyncio.to_thread(hash_password, new_password)
    user.tokens_valid_after = _now()
    await _revoke_all_for_user(db, user.id)
    await db.execute(
        update(EmailToken)
        .where(EmailToken.email == user.email, EmailToken.type == "password_reset", EmailToken.used_at.is_(None))
        .values(used_at=_now())
    )
    audit(db, action="auth.password_reset", tenant_id=None, actor_user_id=user.id, ip=ip)
    await db.commit()


# --- Invitations -----------------------------------------------------------------


async def invite(db: AsyncSession, ctx: RequestContext, *, email: str, role: Role) -> None:
    s = get_settings()
    await ratelimit.hit(db, f"invite:tenant:{ctx.tenant_id}", s.rl_invite_per_tenant, s.rl_invite_window_s)
    email = normalize_email(email)
    already = (
        await db.execute(
            select(Membership)
            .join(User, User.id == Membership.user_id)
            .where(User.email == email, Membership.tenant_id == ctx.tenant_id)
        )
    ).scalar_one_or_none()
    if already is not None:
        raise Conflict("User is already a member", code="already_member")

    tenant = await db.get(Tenant, ctx.tenant_id)
    inviter = await db.get(User, ctx.user_id) if ctx.user_id else None
    assert tenant is not None
    raw = new_opaque_token()
    db.add(
        EmailToken(
            type="invite",
            email=email,
            tenant_id=ctx.tenant_id,
            role=role.value,
            token_hash=hash_token(raw),
            expires_at=_now() + timedelta(seconds=s.invite_ttl_s),
            created_by=ctx.user_id,
        )
    )
    queue_email(
        db,
        template="invite",
        to=email,
        context={
            "link": f"{s.public_ui_url}/accept_invite?token={raw}",
            "tenant_name": tenant.name,
            "role": role.value,
            "inviter": inviter.email if inviter else "An administrator",
            "expires_hours": s.invite_ttl_s // 3600,
        },
    )
    audit(
        db,
        action="member.invited",
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        target_type="email",
        ip=ctx.ip,
        email=email,
        role=role.value,
    )
    await db.commit()


async def accept_invite(db: AsyncSession, *, token: str, password: str | None, ip: str | None) -> TokenPair:
    await ratelimit.hit(db, f"accept:ip:{ip}", 20, 3600)
    tok = await _consume_email_token(db, token, "invite")
    assert tok.tenant_id is not None and tok.role is not None
    user = (await db.execute(select(User).where(User.email == tok.email).with_for_update())).scalar_one_or_none()
    if user is None:
        if not password:
            raise AppError("A password is required to create your account", code="password_required")
        validate_password_policy(password, tok.email)
        user = User(id=uuid.uuid4(), email=tok.email, password_hash=await asyncio.to_thread(hash_password, password))
        db.add(user)
        await db.flush()
    elif not user.is_active:
        raise AppError("This link is invalid or has expired", code="invalid_token")

    exists = await db.get(Membership, (user.id, tok.tenant_id))
    if exists is None:
        db.add(Membership(user_id=user.id, tenant_id=tok.tenant_id, role=tok.role))
    audit(
        db,
        action="member.joined",
        tenant_id=tok.tenant_id,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        ip=ip,
    )
    pair, _ = await _issue_tokens(db, user.id, tok.tenant_id)
    await db.commit()
    return pair


# --- Members -------------------------------------------------------------------


async def _admin_count(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(Membership)
            .where(Membership.tenant_id == tenant_id, Membership.role == Role.ADMIN.value)
        )
    ).scalar_one()


async def _get_member(db: AsyncSession, ctx: RequestContext, user_id: uuid.UUID) -> Membership:
    await db.execute(select(Tenant.id).where(Tenant.id == ctx.tenant_id).with_for_update())
    actor = await db.get(Membership, (ctx.user_id, ctx.tenant_id), populate_existing=True)
    if actor is None or actor.role != Role.ADMIN.value:
        raise Forbidden()
    m = (
        await db.execute(
            select(Membership)
            .where(Membership.tenant_id == ctx.tenant_id, Membership.user_id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if m is None:
        raise NotFound("Member not found")
    return m


async def change_role(db: AsyncSession, ctx: RequestContext, user_id: uuid.UUID, role: Role) -> None:
    if user_id == ctx.user_id:
        raise Forbidden("You cannot change your own role")
    m = await _get_member(db, ctx, user_id)
    if m.role == Role.ADMIN and role != Role.ADMIN and await _admin_count(db, ctx.tenant_id) <= 1:
        raise Conflict("Cannot demote the last admin", code="last_admin")
    old = m.role
    m.role = role.value
    audit(
        db,
        action="member.role_changed",
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        target_type="user",
        target_id=user_id,
        ip=ctx.ip,
        old=old,
        new=role.value,
    )
    await db.commit()


async def remove_member(db: AsyncSession, ctx: RequestContext, user_id: uuid.UUID) -> None:
    if user_id == ctx.user_id:
        raise Forbidden("You cannot remove yourself")
    m = await _get_member(db, ctx, user_id)
    if m.role == Role.ADMIN and await _admin_count(db, ctx.tenant_id) <= 1:
        raise Conflict("Cannot remove the last admin", code="last_admin")
    await db.delete(m)
    await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.tenant_id == ctx.tenant_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=_now())
    )
    audit(
        db,
        action="member.removed",
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        target_type="user",
        target_id=user_id,
        ip=ctx.ip,
    )
    await db.commit()


# --- Bootstrap -----------------------------------------------------------------


async def bootstrap_admin(db: AsyncSession, *, email: str, password: str, tenant_name: str) -> uuid.UUID:
    email = normalize_email(email)
    validate_password_policy(password, email)
    tenant = (await db.execute(select(Tenant).where(Tenant.name == tenant_name))).scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(id=uuid.uuid4(), name=tenant_name)
        db.add(tenant)
    user = (await db.execute(select(User).where(User.email == email).with_for_update())).scalar_one_or_none()
    if user is None:
        user = User(id=uuid.uuid4(), email=email, password_hash=await asyncio.to_thread(hash_password, password))
        db.add(user)
    await db.flush()
    if await db.get(Membership, (user.id, tenant.id)) is None:
        db.add(Membership(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN.value))
    audit(db, action="tenant.bootstrap", tenant_id=tenant.id, actor_user_id=user.id)
    await db.commit()
    return tenant.id
