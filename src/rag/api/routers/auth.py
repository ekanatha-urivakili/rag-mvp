from fastapi import APIRouter, Request, Response, status

from rag.api.schemas import (
    AcceptInviteIn,
    ChangePasswordIn,
    ForgotIn,
    LoginIn,
    RefreshIn,
    ResetIn,
    SignupIn,
    SwitchTenantIn,
    TokenOut,
    VerifySignupIn,
)
from rag.auth import service
from rag.auth.deps import DB, UserCtx, client_ip
from rag.core import ratelimit
from rag.core.config import get_settings
from rag.core.errors import Forbidden, Unauthorized

router = APIRouter(prefix="/v1/auth", tags=["auth"])
public_router = APIRouter(tags=["auth"])


def _set_refresh_cookie(response: Response, pair: service.TokenPair) -> TokenOut:
    s = get_settings()
    response.set_cookie(
        s.refresh_cookie_name,
        pair.refresh_token,
        max_age=s.refresh_token_ttl_s,
        httponly=True,
        secure=s.is_prod,
        samesite="strict",
        path="/v1/auth",
    )
    return TokenOut(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        tenant_id=pair.tenant_id,
    )


def _refresh_token(body: RefreshIn, request: Request) -> str | None:
    if body.refresh_token:
        return body.refresh_token
    cookie = request.cookies.get(get_settings().refresh_cookie_name)
    if cookie:
        # SameSite is a site boundary, not an origin boundary (a sibling subdomain can send this cookie).
        origin = request.headers.get("origin")
        if origin not in get_settings().cors_origins:
            raise Forbidden("Cross-site request blocked")
    return cookie


@router.post("/signup", status_code=status.HTTP_202_ACCEPTED)
async def signup(body: SignupIn, request: Request, db: DB) -> dict[str, str]:
    await service.signup(db, email=body.email, ip=client_ip(request))
    return {"status": "If you can register with this email, a verification link is on its way."}


@router.post("/signup/verify", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def verify_signup(body: VerifySignupIn, request: Request, response: Response, db: DB) -> TokenOut:
    pair = await service.verify_signup(
        db, token=body.token, password=body.password, workspace_name=body.workspace_name, ip=client_ip(request)
    )
    return _set_refresh_cookie(response, pair)


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request, response: Response, db: DB) -> TokenOut:
    pair = await service.login(
        db, email=body.email, password=body.password, tenant_id=body.tenant_id, ip=client_ip(request)
    )
    return _set_refresh_cookie(response, pair)


@router.post("/refresh", response_model=TokenOut)
async def refresh(body: RefreshIn, request: Request, response: Response, db: DB) -> TokenOut:
    s = get_settings()
    await ratelimit.hit(db, f"refresh:ip:{client_ip(request)}", s.rl_refresh_per_ip, s.rl_refresh_window_s)
    raw = _refresh_token(body, request)
    if not raw:
        raise Unauthorized("Refresh token required")
    return _set_refresh_cookie(response, await service.refresh(db, raw))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshIn, response: Response, ctx: UserCtx, db: DB) -> None:
    await service.logout(db, ctx, body.refresh_token)
    response.delete_cookie(get_settings().refresh_cookie_name, path="/v1/auth")


@router.post("/switch-tenant", response_model=TokenOut)
async def switch_tenant(body: SwitchTenantIn, response: Response, ctx: UserCtx, db: DB) -> TokenOut:
    return _set_refresh_cookie(response, await service.switch_tenant(db, ctx, body.tenant_id))


@router.post("/password/forgot", status_code=status.HTTP_202_ACCEPTED)
async def forgot(body: ForgotIn, request: Request, db: DB) -> dict[str, str]:
    await service.forgot_password(db, email=body.email, ip=client_ip(request))
    return {"status": "If the account exists, a reset email has been sent."}


@router.post("/password/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset(body: ResetIn, request: Request, db: DB) -> None:
    await service.reset_password(db, token=body.token, new_password=body.new_password, ip=client_ip(request))


@public_router.post("/v1/invitations/accept", response_model=TokenOut)
async def accept_invite(body: AcceptInviteIn, request: Request, response: Response, db: DB) -> TokenOut:
    pair = await service.accept_invite(db, token=body.token, password=body.password, ip=client_ip(request))
    return _set_refresh_cookie(response, pair)


@router.post("/password/change", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(body: ChangePasswordIn, ctx: UserCtx, db: DB) -> None:
    await service.change_password(db, ctx, body.current_password, body.new_password)
