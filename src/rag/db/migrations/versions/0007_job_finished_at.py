"""Record job completion time for the terminal-failure alert window."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    # Historic completion times are unknown; the last claim is the closest available timestamp.
    op.execute("UPDATE jobs SET finished_at = coalesce(locked_at, created_at) WHERE status IN ('done', 'failed')")
    op.create_index("ix_jobs_finished", "jobs", ["status", "finished_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_finished", table_name="jobs")
    op.drop_column("jobs", "finished_at")
