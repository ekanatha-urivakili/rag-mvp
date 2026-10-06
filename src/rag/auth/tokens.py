import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

import jwt

from rag.core.config import get_settings
from rag.core.errors import Unauthorized

_ALGORITHM = "HS256"
API_KEY_PREFIX = "rk_"


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def create_access_token(user_id: uuid.UUID, tenant_id: uuid.UUID) -> tuple[str, int]:
    s = get_settings()
    now = datetime.now(UTC).timestamp()  # sub-second iat so revocation by timestamp is exact
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "iss": s.jwt_issuer,
        "aud": s.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": now + s.access_token_ttl_s,
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    token = jwt.encode(claims, s.jwt_secret.get_secret_value(), algorithm=_ALGORITHM)
    return token, s.access_token_ttl_s


def decode_access_token(token: str) -> dict[str, Any]:
    s = get_settings()
    try:
        # Algorithm pinned: rejects "none" and alg-confusion attacks.
        claims: dict[str, Any] = jwt.decode(
            token,
            s.jwt_secret.get_secret_value(),
            algorithms=[_ALGORITHM],
            audience=s.jwt_audience,
            issuer=s.jwt_issuer,
            options={"require": ["exp", "iat", "nbf", "sub", "tid", "iss", "aud", "jti"]},
            leeway=10,
        )
    except jwt.PyJWTError as e:
        raise Unauthorized("Invalid or expired token") from e
    if claims.get("typ") != "access":
        raise Unauthorized("Invalid token type")
    try:
        uuid.UUID(claims["sub"])
        uuid.UUID(claims["tid"])
    except (ValueError, TypeError) as e:
        raise Unauthorized("Invalid token subject") from e
    return claims


def new_api_key() -> tuple[str, str, str]:
    """Returns (plaintext, prefix, sha256 hash). Plaintext is shown once and never stored."""
    prefix = secrets.token_hex(6)
    plaintext = f"{API_KEY_PREFIX}{prefix}_{secrets.token_urlsafe(32)}"
    return plaintext, prefix, hash_token(plaintext)


def parse_api_key_prefix(raw: str) -> str | None:
    if not raw.startswith(API_KEY_PREFIX):
        return None
    parts = raw[len(API_KEY_PREFIX) :].split("_", 1)
    if len(parts) != 2 or len(parts[0]) != 12 or not parts[1]:
        return None
    return parts[0]
