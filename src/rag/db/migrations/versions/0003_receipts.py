"""Receipt extraction: structured receipts table and live document progress."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("progress", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    money = sa.Numeric(14, 2)
    op.create_table(
        "receipts",
        sa.Column("document_id", sa.UUID(), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("merchant_name", sa.String(200), nullable=True),
        sa.Column("merchant_address", sa.String(500), nullable=True),
        sa.Column("merchant_phone", sa.String(50), nullable=True),
        sa.Column("purchased_on", sa.Date(), nullable=True),
        sa.Column("purchased_time", sa.Time(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("discounts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=True),
        sa.Column("subtotal", money, nullable=True),
        sa.Column("discount_total", money, nullable=True),
        sa.Column("tax", money, nullable=True),
        sa.Column("tip", money, nullable=True),
        sa.Column("total", money, nullable=True),
        sa.Column("payment_method", sa.String(16), nullable=True),
        sa.Column("card_brand", sa.String(32), nullable=True),
        sa.Column("card_last4", sa.String(4), nullable=True),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_receipts_tenant_purchased", "receipts", ["tenant_id", "purchased_on"])


def downgrade() -> None:
    op.drop_index("ix_receipts_tenant_purchased", table_name="receipts")
    op.drop_table("receipts")
    op.drop_column("documents", "progress")
