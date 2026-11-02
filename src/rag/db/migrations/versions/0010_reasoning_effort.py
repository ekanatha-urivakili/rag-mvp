"""Remember the selected reasoning effort with each conversation."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("reasoning_effort", sa.String(16)))


def downgrade() -> None:
    op.drop_column("conversations", "reasoning_effort")
