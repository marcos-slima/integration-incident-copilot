"""Add tenant_id to llm_credentials table (DA-79 multi-tenancy).

Revision ID: 023_add_tenant_id_credentials
Revises: 022_add_tenant_id
Create Date: 2026-10-19

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "023_add_tenant_id_credentials"
down_revision: str | None = "022_add_tenant_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add tenant_id column to llm_credentials table
    op.add_column("llm_credentials", sa.Column("tenant_id", sa.String(), nullable=True))

    # Create index on tenant_id for filtering
    op.create_index(
        op.f("ix_llm_credentials_tenant"), "llm_credentials", ["tenant_id"], unique=False
    )

    # Create unique constraint for tenant-specific credentials (PostgreSQL)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_credentials_tenant_origin
        ON llm_credentials (tenant_id, provider_origin)
        WHERE tenant_id IS NOT NULL
    """)

    # Create unique constraint for global credentials (PostgreSQL)
    # Global credentials have tenant_id IS NULL and provider_origin must be unique
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_credentials_global_origin
        ON llm_credentials (provider_origin)
        WHERE tenant_id IS NULL
    """)

    # Add comment for documentation
    op.execute("""
        COMMENT ON COLUMN llm_credentials.tenant_id IS 'DA-79: tenant identifier for credential isolation. NULL = global credential'
    """)


def downgrade() -> None:
    # Drop constraints first
    op.drop_index("uq_llm_credentials_global_origin", table_name="llm_credentials")
    op.drop_index("uq_llm_credentials_tenant_origin", table_name="llm_credentials")
    op.drop_index(op.f("ix_llm_credentials_tenant"), table_name="llm_credentials")
    op.drop_column("llm_credentials", "tenant_id")
