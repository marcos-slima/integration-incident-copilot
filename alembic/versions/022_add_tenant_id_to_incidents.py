"""Add tenant_id to incidents table (DA-79 multi-tenancy).

Revision ID: 022_add_tenant_id
Revises: aa5bc901f443
Create Date: 2026-10-19

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "022_add_tenant_id"
down_revision: str | None = "aa5bc901f443"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add tenant_id column to incidents table
    op.add_column("incidents", sa.Column("tenant_id", sa.String(), nullable=True))

    # Create index on tenant_id for filtering
    op.create_index(op.f("ix_incidents_tenant_id"), "incidents", ["tenant_id"], unique=False)

    # Add comment for documentation
    op.execute("""
        COMMENT ON COLUMN incidents.tenant_id IS 'DA-79: tenant identifier for multi-tenancy isolation'
    """)


def downgrade() -> None:
    op.drop_index(op.f("ix_incidents_tenant_id"), table_name="incidents")
    op.drop_column("incidents", "tenant_id")
