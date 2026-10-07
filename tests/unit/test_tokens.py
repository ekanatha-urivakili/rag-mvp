import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from rag.auth.tokens import create_access_token, decode_access_token, new_api_key, parse_api_key_prefix
from rag.core.config import get_settings
from rag.core.errors import Unauthorized


def _claims(**over: object) -> dict[str, object]:
    s = get_settings()
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "sub": str(uuid.uuid4()),
        "tid": str(uuid.uuid4()),
        "sid": str(uuid.uuid4()),
        "iss": s.jwt_issuer,
        "aud": s.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
        "jti": "x",
        "typ": "access",
    }
    return {**base, **over}


def _sign(claims: dict[str, object], key: str | None = None, alg: str = "HS256") -> str:
    return jwt.encode(claims, key or get_settings().jwt_secret.get_secret_value(), algorithm=alg)


def test_roundtrip() -> None:
    uid, tid = uuid.uuid4(), uuid.uuid4()
    token, ttl = create_access_token(uid, tid, uuid.uuid4())
    claims = decode_access_token(token)
    assert claims["sub"] == str(uid) and claims["tid"] == str(tid) and ttl == 900


@pytest.mark.parametrize(
    "token_factory",
    [
        lambda: _sign(_claims(exp=datetime.now(UTC) - timedelta(minutes=1))),  # expired
        lambda: _sign(_claims(aud="someone-else")),  # wrong audience
        lambda: _sign(_claims(iss="evil")),  # wrong issuer
        lambda: _sign(_claims(typ="refresh")),  # wrong type
        lambda: _sign(_claims(), key="a-different-secret-that-is-long-enough-123"),  # forged
        lambda: jwt.encode(_claims(), key=None, algorithm="none"),  # alg=none
        lambda: _sign({k: v for k, v in _claims().items() if k != "tid"}),  # missing claim
        lambda: _sign(_claims(sub="not-a-uuid")),
    ],
)
def test_rejects_bad_tokens(token_factory) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(Unauthorized):
        decode_access_token(token_factory())


def test_tampered_payload_rejected() -> None:
    token, _ = create_access_token(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    header, payload, sig = token.split(".")
    with pytest.raises(Unauthorized):
        decode_access_token(f"{header}.{payload[:-2]}AA.{sig}")


def test_api_key_format() -> None:
    plaintext, prefix, digest = new_api_key()
    assert plaintext.startswith(f"rk_{prefix}_") and len(digest) == 64
    assert parse_api_key_prefix(plaintext) == prefix
    assert parse_api_key_prefix("rk_short_x") is None
    assert parse_api_key_prefix("Bearer something") is None
