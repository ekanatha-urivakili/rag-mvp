from typing import Annotated

from fastapi import APIRouter, Cookie, Request, Response, status

from rag.api.schemas import AcceptInviteIn, ForgotIn, LoginIn, RefreshIn, ResetIn, SwitchTenantIn, TokenOut
from rag.auth import service
from rag.auth.deps import DB, UserCtx, client_ip
from rag.core.config import get_settings
from rag.core.errors import Unauthorized

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


RefreshCookie = Annotated[str | None, Cookie(alias="rag_refresh")]


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request, response: Response, db: DB) -> TokenOut:
    pair = await service.login(
        db, email=body.email, password=body.password, tenant_id=body.tenant_id, ip=client_ip(request)
    )
    return _set_refresh_cookie(response, pair)


@router.post("/refresh", response_model=TokenOut)
async def refresh(body: RefreshIn, response: Response, db: DB, cookie: RefreshCookie = None) -> TokenOut:
    raw = body.refresh_token or cookie
    if not raw:
        raise Unauthorized("Refresh token required")
    return _set_refresh_cookie(response, await service.refresh(db, raw))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshIn, response: Response, ctx: UserCtx, db: DB, cookie: RefreshCookie = None) -> None:
    await service.logout(db, ctx, body.refresh_token or cookie)
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
