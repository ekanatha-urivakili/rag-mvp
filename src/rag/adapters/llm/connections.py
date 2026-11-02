import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag.adapters.llm.base import RouteEntry
from rag.adapters.llm.router import get_router
from rag.api.schemas import ChatModelOut
from rag.core.config import get_settings
from rag.core.errors import AppError, NotFound
from rag.db.models import ModelConnection
from rag.domain.models import RequestContext


@dataclass(frozen=True)
class ChatSelection:
    id: str
    entry: RouteEntry
    api_key: SecretStr | None = None


def _cipher() -> AESGCM:
    s = get_settings()
    secret = s.model_keys_encryption_key
    if secret is None:
        if s.is_prod:
            raise AppError("Personal API keys are unavailable until MODEL_KEYS_ENCRYPTION_KEY is configured")
        secret = s.jwt_secret
    key = hmac.digest(secret.get_secret_value().encode(), b"rag:model-api-keys:v1", hashlib.sha256)
    return AESGCM(key)


def _identity(row: ModelConnection) -> bytes:
    return f"{row.id}:{row.tenant_id}:{row.user_id}:{row.provider}:{row.model}".encode()


def encrypt_key(row: ModelConnection, api_key: SecretStr) -> str:
    nonce = secrets.token_bytes(12)
    encrypted = _cipher().encrypt(nonce, api_key.get_secret_value().encode(), _identity(row))
    return base64.urlsafe_b64encode(nonce + encrypted).decode()


def decrypt_key(row: ModelConnection) -> SecretStr | None:
    if row.encrypted_api_key is None:
        return None
    raw = base64.urlsafe_b64decode(row.encrypted_api_key)
    try:
        return SecretStr(_cipher().decrypt(raw[:12], raw[12:], _identity(row)).decode())
    except InvalidTag:
        raise AppError("This model's API key can no longer be decrypted. Remove and add the model again.") from None


def server_models() -> list[ChatModelOut]:
    return [
        ChatModelOut(id=f"server-{entry.provider}", provider=entry.provider, model=entry.model, source="server")
        for entry in get_router().chat_entries()
    ]


async def personal_models(db: AsyncSession, ctx: RequestContext) -> list[ModelConnection]:
    return list(
        (
            await db.scalars(
                select(ModelConnection)
                .where(ModelConnection.user_id == ctx.user_id, ModelConnection.tenant_id == ctx.tenant_id)
                .order_by(ModelConnection.created_at, ModelConnection.id)
            )
        ).all()
    )


def connection_out(row: ModelConnection) -> ChatModelOut:
    return ChatModelOut(id=str(row.id), provider=row.provider, model=row.model, source="personal")


async def resolve_chat_model(db: AsyncSession, ctx: RequestContext, model_id: str | None) -> ChatSelection:
    entries = get_router().chat_entries()
    if model_id is None and entries:
        model_id = f"server-{entries[0].provider}"
    for entry in entries:
        if model_id == f"server-{entry.provider}":
            return ChatSelection(model_id, entry)
    try:
        connection_id = uuid.UUID(model_id or "")
    except ValueError:
        raise NotFound("Choose an available model or add a model connection") from None
    row = await db.scalar(
        select(ModelConnection).where(
            ModelConnection.id == connection_id,
            ModelConnection.user_id == ctx.user_id,
            ModelConnection.tenant_id == ctx.tenant_id,
        )
    )
    if row is None:
        raise NotFound("Model connection not found. Choose another model.")
    options = next(
        (entry.options.copy() for entry in entries if entry.provider == row.provider and entry.model == row.model),
        {"think": False} if row.provider == "ollama" else {},
    )
    return ChatSelection(str(row.id), RouteEntry(row.provider, row.model, 90, options), decrypt_key(row))
