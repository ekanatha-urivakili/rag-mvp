import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict

from rag.auth.rbac import Permission, Role


class RequestContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: uuid.UUID | None  # None for API-key (service account) principals
    tenant_id: uuid.UUID
    role: Role
    api_key_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    ip: str | None = None

    def can(self, permission: Permission) -> bool:
        from rag.auth.rbac import ROLE_PERMISSIONS

        return permission in ROLE_PERMISSIONS[self.role]


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ScoredChunk(BaseModel):
    point_id: str
    doc_id: str
    doc_version: int
    chunk_index: int
    text: str
    heading_path: str
    source: str
    page: int | None
    score: float


class Citation(BaseModel):
    n: int
    doc_id: str
    source: str
    page: int | None
    snippet: str


class ModelUsage(BaseModel):
    purpose: str
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    fallback_used: bool = False
