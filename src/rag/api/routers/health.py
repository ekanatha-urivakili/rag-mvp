import hmac

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from rag.adapters.storage import get_storage
from rag.adapters.vectorstore import get_vectorstore
from rag.core import metrics
from rag.core.config import get_settings
from rag.core.errors import NotFound, Unauthorized
from rag.db.session import get_sessionmaker

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> JSONResponse:
    s = get_settings()
    checks: dict[str, str] = {}
    try:
        async with get_sessionmaker()() as db:
            await db.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "down"
    checks["qdrant"] = "ok" if await get_vectorstore().healthy() else "down"
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            r = await client.get(f"{get_settings().ollama_base_url}/api/version")
        checks["ollama"] = "ok" if r.is_success else "degraded"
    except httpx.HTTPError:
        checks["ollama"] = "degraded"  # cloud fallback covers this; not a readiness failure
    checks["storage"] = "ok" if await get_storage().healthy() else "down"
    ollama_required = s.embedding_provider == "ollama" or not (
        s.anthropic_api_key and s.anthropic_api_key.get_secret_value().strip()
    )
    ready = all(checks[k] == "ok" for k in ("postgres", "qdrant", "storage")) and (
        not ollama_required or checks["ollama"] == "ok"
    )
    return JSONResponse({"ready": ready, "checks": checks}, status_code=200 if ready else 503)


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics(request: Request) -> Response:
    """Prometheus scrape target. Bearer METRICS_TOKEN when set; without one it is open outside prod only."""
    s = get_settings()
    if s.metrics_token is None:
        if s.is_prod:
            raise NotFound("Not found")
    else:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").encode()
        if not hmac.compare_digest(supplied, s.metrics_token.get_secret_value().encode()):
            raise Unauthorized("Invalid metrics token")
    async with get_sessionmaker()() as db:
        body, content_type = await metrics.render(db)
    return Response(body, media_type=content_type)
