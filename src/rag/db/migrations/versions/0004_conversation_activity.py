"""Chat history: track last activity per conversation so history is ordered and paginated by recency."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute(
        "UPDATE conversations c SET updated_at = COALESCE("
        "(SELECT max(m.created_at) FROM messages m WHERE m.conversation_id = c.id), c.created_at)"
    )
    op.drop_index("ix_conversations_owner", table_name="conversations")
    op.create_index("ix_conversations_owner_activity", "conversations", ["tenant_id", "user_id", "updated_at", "id"])
    op.create_index("ix_messages_conversation_created", "messages", ["conversation_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_messages_conversation_created", table_name="messages")
    op.drop_index("ix_conversations_owner_activity", table_name="conversations")
    op.create_index("ix_conversations_owner", "conversations", ["tenant_id", "user_id", "created_at"])
    op.drop_column("conversations", "updated_at")
