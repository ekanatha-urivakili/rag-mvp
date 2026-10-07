"""Allow email verification tokens for self-service signup."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_email_token_type", "email_tokens", type_="check")
    op.create_check_constraint("ck_email_token_type", "email_tokens", "type in ('invite','password_reset','signup')")


def downgrade() -> None:
    op.execute("DELETE FROM email_tokens WHERE type = 'signup'")
    op.drop_constraint("ck_email_token_type", "email_tokens", type_="check")
    op.create_check_constraint("ck_email_token_type", "email_tokens", "type in ('invite','password_reset')")
