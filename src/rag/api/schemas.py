import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationInfo, field_validator

from rag.auth.rbac import Role
from rag.domain.models import Citation


class In(BaseModel):
    # Reject unknown fields: blocks mass-assignment (OWASP API3) and typos.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    @field_validator("*", mode="before")
    @classmethod
    def normalize_text(cls, value: object, info: ValidationInfo) -> object:
        if isinstance(value, str) and info.field_name not in {"password", "new_password"}:
            return value.strip()
        return value


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Auth ---
class LoginIn(In):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    tenant_id: uuid.UUID | None = None


class RefreshIn(In):
    refresh_token: str | None = Field(default=None, max_length=256)


class TokenOut(Out):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_in: int
    tenant_id: uuid.UUID


class ForgotIn(In):
    email: EmailStr


class ResetIn(In):
    token: str = Field(min_length=16, max_length=256)
    new_password: str = Field(min_length=1, max_length=128)


class SwitchTenantIn(In):
    tenant_id: uuid.UUID


class InviteIn(In):
    email: EmailStr
    role: Role


class AcceptInviteIn(In):
    token: str = Field(min_length=16, max_length=256)
    password: str | None = Field(default=None, max_length=128)


class TenantOut(Out):
    id: uuid.UUID
    name: str
    role: Role


class MeOut(Out):
    user_id: uuid.UUID | None
    email: str | None
    tenant: TenantOut
    permissions: list[str]
    tenants: list[TenantOut]
    debug_enabled: bool


# --- Members ---
class MemberOut(Out):
    user_id: uuid.UUID
    email: str
    role: Role
    is_active: bool
    joined_at: datetime


class InvitationOut(Out):
    id: uuid.UUID
    email: str
    role: Role
    expires_at: datetime
    created_at: datetime


class RoleIn(In):
    role: Role


# --- API keys ---
class ApiKeyIn(In):
    name: str = Field(min_length=1, max_length=100)
    role: Role


class ApiKeyOut(Out):
    id: uuid.UUID
    name: str
    role: Role
    key_prefix: str
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreatedOut(ApiKeyOut):
    api_key: str  # shown once


# --- Audit ---
class AuditOut(Out):
    id: int
    actor_user_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    metadata: dict[str, Any]
    ip: str | None
    created_at: datetime


# --- Documents ---
class DocumentOut(Out):
    id: uuid.UUID
    title: str
    mime_type: str
    size_bytes: int
    version: int
    status: str
    error: str | None
    chunk_count: int = 0
    created_at: datetime
    updated_at: datetime


class UploadAccepted(Out):
    document_id: uuid.UUID
    job_id: uuid.UUID | None
    status: str


class Page[T](Out):
    items: list[T]
    total: int


# --- Chat ---
class ChatFilters(In):
    doc_id: uuid.UUID | None = None


class ChatIn(In):
    conversation_id: uuid.UUID | None = None
    message: str = Field(min_length=1, max_length=4000)
    filters: ChatFilters | None = None


class ConversationOut(Out):
    id: uuid.UUID
    title: str
    created_at: datetime


class MessageOut(Out):
    id: uuid.UUID
    role: str
    content: str
    citations: list[Citation]
    created_at: datetime


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut]


class FeedbackIn(In):
    rating: Literal[-1, 1]
    comment: str | None = Field(default=None, max_length=2000)
