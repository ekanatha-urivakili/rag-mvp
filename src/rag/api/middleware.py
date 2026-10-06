import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from rag.core.config import get_settings
from rag.core.logging import log_extra, request_id_var

log = logging.getLogger("rag.access")


class SecurityMiddleware(BaseHTTPMiddleware):
    """Request ID, body-size cap, security headers, and access log without query strings (tokens)."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._settings = get_settings()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = uuid.uuid4().hex
        request_id_var.set(rid)
        length = request.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > self._settings.max_request_body_bytes):
            return JSONResponse(
                {"error": {"code": "payload_too_large", "message": "Request body too large"}}, status_code=413
            )
        t0 = time.perf_counter()
        response = await call_next(request)
        h = response.headers
        h["X-Request-ID"] = rid
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        h["Cross-Origin-Resource-Policy"] = "same-site"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/docs"):
            h["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                "img-src 'self' data: https://fastapi.tiangolo.com; "
                "frame-ancestors 'none'"
            )
        else:
            h["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        h.setdefault("Cache-Control", "no-store")
        if self._settings.is_prod:
            h["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        log.info(
            "request",
            extra=log_extra(
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                ms=int((time.perf_counter() - t0) * 1000),
            ),
        )
        return response
