"""Private encrypted model connections and per-conversation model selection."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_connections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("encrypted_api_key", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("provider in ('openai','anthropic','ollama')", name="ck_model_connection_provider"),
    )
    op.create_index("ix_model_connections_owner", "model_connections", ["tenant_id", "user_id"])
    op.add_column("conversations", sa.Column("model_id", sa.String(80)))


def downgrade() -> None:
    op.drop_column("conversations", "model_id")
    op.drop_table("model_connections")
