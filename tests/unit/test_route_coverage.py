"""Every route must be guarded: either an explicit public allowlist entry or an auth dependency.
A new route without `require(...)` / auth fails CI."""

from fastapi.routing import APIRoute

from rag.api.main import app
from rag.auth.deps import get_context, require_user

PUBLIC = {
    ("GET", "/healthz"),
    ("GET", "/readyz"),
    ("POST", "/v1/auth/login"),
    ("POST", "/v1/auth/signup"),
    ("POST", "/v1/auth/signup/verify"),
    ("POST", "/v1/auth/refresh"),
    ("POST", "/v1/auth/password/forgot"),
    ("POST", "/v1/auth/password/reset"),
    ("POST", "/v1/invitations/accept"),
}


def _guarded(route: APIRoute) -> bool:
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        if dep.call in (get_context, require_user) or hasattr(dep.call, "__rag_permission__"):
            return True
        stack.extend(dep.dependencies)
    return False


def test_every_route_is_public_by_intent_or_guarded() -> None:
    unguarded = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in {"/openapi.json", "/docs"}:
            continue
        for method in route.methods:
            if (method, route.path) not in PUBLIC and not _guarded(route):
                unguarded.append((method, route.path))
    assert unguarded == []
