from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_WEAK_SECRETS = {"changeme", "secret", "change-me", "replace-me"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://rag:rag@localhost:5432/rag"

    # --- Auth ---
    jwt_secret: SecretStr
    jwt_issuer: str = "rag-api"
    jwt_audience: str = "rag-clients"
    access_token_ttl_s: int = 15 * 60
    refresh_token_ttl_s: int = 14 * 24 * 3600
    invite_ttl_s: int = 72 * 3600
    password_reset_ttl_s: int = 30 * 60
    signup_ttl_s: int = Field(default=30 * 60, gt=0)
    signup_enabled: bool = True
    rl_signup_per_ip: int = Field(default=5, gt=0)
    rl_signup_window_s: int = Field(default=3600, gt=0)
    password_min_length: int = 12
    password_max_length: int = 128
    refresh_cookie_name: str = "rag_refresh"

    # --- HTTP hardening ---
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:8501"])
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1", "api"])
    public_ui_url: str = "http://localhost:8501"
    max_request_body_bytes: int = 30 * 1024 * 1024

    # --- Rate limits (requests per window) ---
    rl_login_per_ip: int = 20
    rl_login_per_email: int = 5
    rl_login_window_s: int = 900
    rl_forgot_per_ip: int = 5
    rl_forgot_window_s: int = 3600
    rl_chat_per_tenant: int = 120
    rl_chat_per_user: int = 30
    rl_chat_window_s: int = 60
    rl_upload_per_tenant: int = 60
    rl_upload_window_s: int = 3600

    # --- Uploads ---
    max_upload_bytes: int = 25 * 1024 * 1024
    max_docx_uncompressed_bytes: int = 200 * 1024 * 1024

    # --- Storage ---
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: SecretStr = SecretStr("minioadmin")
    s3_secret_key: SecretStr = SecretStr("minioadmin")
    s3_bucket: str = "rag-uploads"
    s3_region: str = "us-east-1"

    # --- Qdrant ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: SecretStr | None = None
    qdrant_alias: str = "chunks_current"
    qdrant_timeout_s: int = 5

    # --- Embeddings / retrieval ---
    embedding_provider: Literal["openai", "ollama"] = "ollama"
    embedding_model: str = "bge-m3"
    embedding_dim: int = 1024
    embedding_batch_size: int = 64
    sparse_model: str = "Qdrant/bm25"
    reranker_model: str = "BAAI/bge-reranker-base"
    reranker_enabled: bool = True
    retrieval_prefetch: int = 40
    retrieval_top_k: int = 8
    grade_threshold: float = 0.0
    chunk_tokens: int = 500
    chunk_overlap: int = 50

    # --- LLM providers ---
    models_config_path: str = "config/models.yaml"
    ollama_base_url: str = "http://localhost:11434"
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    openai_generation_model: str = "gpt-5-mini"
    openai_judge_model: str = "gpt-5-mini"
    generation_max_tokens: int = 2048
    history_turns: int = 6
    max_question_chars: int = 4000

    # --- Email ---
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_tls: bool = False
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str = "RAG <no-reply@localhost>"
    smtp_timeout_s: int = 10

    # --- Worker ---
    worker_poll_interval_s: float = 1.0
    job_max_attempts: int = 3

    @field_validator("jwt_secret")
    @classmethod
    def _strong_jwt_secret(cls, v: SecretStr) -> SecretStr:
        raw = v.get_secret_value()
        if len(raw) < 32 or raw.lower() in _WEAK_SECRETS:
            raise ValueError("JWT_SECRET must be at least 32 random characters")
        return v

    @model_validator(mode="after")
    def _prod_guards(self) -> "Settings":
        if self.env == "prod":
            if any(o == "*" for o in self.cors_origins):
                raise ValueError("CORS wildcard is not allowed in prod")
            if "*" in self.allowed_hosts:
                raise ValueError("ALLOWED_HOSTS wildcard is not allowed in prod")
            if any(not o.startswith("https://") for o in self.cors_origins):
                raise ValueError("CORS origins must be https in prod")
            if not self.qdrant_api_key or not self.qdrant_api_key.get_secret_value().strip():
                raise ValueError("QDRANT_API_KEY is required in prod")
            if self.s3_secret_key.get_secret_value().lower() in {"", "minioadmin", *_WEAK_SECRETS}:
                raise ValueError("S3_SECRET_KEY must be configured in prod")
            if not self.public_ui_url.startswith("https://"):
                raise ValueError("PUBLIC_UI_URL must be https in prod")
        return self

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
