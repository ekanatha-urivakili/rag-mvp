"""Rolling conversation summary: conversations.summary/summarized_count and the summarize_conversation job type."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_OLD_TYPES = "'ingest_document','send_email','reindex','purge_document','judge_answer'"


def upgrade() -> None:
    op.add_column("conversations", sa.Column("summary", sa.Text, nullable=True))
    op.add_column(
        "conversations", sa.Column("summarized_count", sa.Integer, nullable=False, server_default=sa.text("0"))
    )
    op.drop_constraint("ck_job_type", "jobs", type_="check")
    op.create_check_constraint("ck_job_type", "jobs", f"type in ({_OLD_TYPES},'summarize_conversation')")


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE type = 'summarize_conversation'")
    op.drop_constraint("ck_job_type", "jobs", type_="check")
    op.create_check_constraint("ck_job_type", "jobs", f"type in ({_OLD_TYPES})")
    op.drop_column("conversations", "summarized_count")
    op.drop_column("conversations", "summary")
