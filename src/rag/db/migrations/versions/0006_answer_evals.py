"""Sampled online answer grading: answer_evals table and the judge_answer job type."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_OLD_TYPES = "'ingest_document','send_email','reindex','purge_document'"


def upgrade() -> None:
    op.create_table(
        "answer_evals",
        sa.Column("message_id", UUID(as_uuid=True), sa.ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("faithfulness", sa.Float, nullable=False),
        sa.Column("relevancy", sa.Float, nullable=False),
        sa.Column("judge_provider", sa.String(32), nullable=False),
        sa.Column("judge_model", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_answer_evals_created", "answer_evals", ["created_at"])
    op.drop_constraint("ck_job_type", "jobs", type_="check")
    op.create_check_constraint("ck_job_type", "jobs", f"type in ({_OLD_TYPES},'judge_answer')")


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE type = 'judge_answer'")
    op.drop_constraint("ck_job_type", "jobs", type_="check")
    op.create_check_constraint("ck_job_type", "jobs", f"type in ({_OLD_TYPES})")
    op.drop_index("ix_answer_evals_created", table_name="answer_evals")
    op.drop_table("answer_evals")
