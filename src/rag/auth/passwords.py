from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from rag.core.config import get_settings
from rag.core.errors import AppError

_hasher = PasswordHash((Argon2Hasher(),))
# Verified against when the user doesn't exist so response timing doesn't reveal account existence.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing-equalisation")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> tuple[bool, str | None]:
    """Returns (ok, upgraded_hash). upgraded_hash is set when hashing parameters have been strengthened."""
    if password_hash is None:
        _hasher.verify(password, _DUMMY_HASH)
        return False, None
    return _hasher.verify_and_update(password, password_hash)


def validate_password_policy(password: str, email: str) -> None:
    s = get_settings()
    if len(password) < s.password_min_length:
        raise AppError(f"Password must be at least {s.password_min_length} characters", code="weak_password")
    if len(password) > s.password_max_length:
        raise AppError(f"Password must be at most {s.password_max_length} characters", code="weak_password")
    local = email.split("@", 1)[0].lower()
    if len(local) >= 4 and local in password.lower():
        raise AppError("Password must not contain your email address", code="weak_password")
    if len(set(password)) < 5:
        raise AppError("Password is too repetitive", code="weak_password")
