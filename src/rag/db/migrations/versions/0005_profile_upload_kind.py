"""Profile names and separate receipt uploads."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("name", sa.String(100), nullable=True))
    op.add_column("documents", sa.Column("kind", sa.String(16), server_default="document", nullable=False))
    op.execute("UPDATE documents SET kind = 'receipt' WHERE id IN (SELECT document_id FROM receipts)")
    op.drop_index("uq_documents_tenant_hash_live", table_name="documents")
    op.create_index(
        "uq_documents_tenant_hash_live",
        "documents",
        ["tenant_id", "content_hash", "kind"],
        unique=True,
        postgresql_where=sa.text("status <> 'deleted'"),
    )


def downgrade() -> None:
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM documents WHERE status <> 'deleted' "
                "GROUP BY tenant_id, content_hash HAVING count(*) > 1 LIMIT 1"
            )
        )
        .first()
    )
    if duplicates is not None:
        raise RuntimeError("Duplicate files across libraries: restore the pre-migration backup to downgrade")
    op.drop_index("uq_documents_tenant_hash_live", table_name="documents")
    op.create_index(
        "uq_documents_tenant_hash_live",
        "documents",
        ["tenant_id", "content_hash"],
        unique=True,
        postgresql_where=sa.text("status <> 'deleted'"),
    )
    op.drop_column("documents", "kind")
    op.drop_column("users", "name")
