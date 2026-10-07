import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.body_limit import RequestBodyLimitMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from rag.api.middleware import SecurityMiddleware
from rag.api.routers import apikeys, audit, auth, chat, documents, health, me, members, receipts
from rag.core.config import get_settings
from rag.core.errors import AppError, RateLimited
from rag.core.logging import configure_logging, request_id_var

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    from rag.adapters.storage import get_storage
    from rag.adapters.vectorstore import get_vectorstore

    await get_vectorstore().ensure_collection()
    await get_storage().ensure_bucket()
    await _warm_local_models()
    yield


async def _warm_local_models() -> None:
    """Load the local orchestration model before the first user request (cold load exceeds its timeout)."""
    from rag.adapters.llm.router import get_router

    try:
        await asyncio.wait_for(get_router().warm_local_models(), timeout=120)
    except Exception:
        log.warning("model_warmup_failed", exc_info=True)


def _error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message, "request_id": request_id_var.get()}},
        status_code=status,
        headers=headers,
    )


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level)
    app = FastAPI(
        title="RAG MVP API",
        version="1.0.0",
        lifespan=lifespan,
        # API inventory is not public in prod (OWASP API9).
        docs_url=None if s.is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if s.is_prod else "/openapi.json",
    )

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, RateLimited) else None
        if exc.status_code == 401:
            headers = {"WWW-Authenticate": "Bearer"}
        return _error(exc.status_code, exc.code, exc.message, headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo submitted values back (they may contain passwords or tokens).
        details = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(
            {
                "error": {
                    "code": "validation_error",
                    "message": "Invalid request",
                    "details": details,
                    "request_id": request_id_var.get(),
                }
            },
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error")
        return _error(500, "internal_error", "Internal server error")

    for r in (
        health.router,
        auth.router,
        auth.public_router,
        me.router,
        members.router,
        apikeys.router,
        audit.router,
        documents.router,
        receipts.router,
        chat.router,
    ):
        app.include_router(r)

    app.add_middleware(RequestBodyLimitMiddleware, max_body_size=s.max_request_body_bytes)
    app.add_middleware(SecurityMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key"],
        max_age=600,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=s.allowed_hosts)
    return app


app = create_app()
