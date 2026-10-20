"""Align DA-79 tenant_id length and indexes with the ORM.

Revision ID: 027_align_da79_tenant_id
Revises: 026_add_knowledge_document_versions_table
Create Date: 2026-10-20

The DA-79 migrations (022/023) created `tenant_id` as `sa.String()` (VARCHAR
without length), while the ORM declares `String(128)`. They also missed the
composite index `ix_incidents_tenant_created` that the ORM declares. Both
divergences make `alembic check` propose schema changes after `upgrade head`.

This migration is additive: it only narrows the type to the length the ORM
already assumed and creates the missing composite index.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by alembic.
revision: str = "027_align_da79_tenant_id"
down_revision: str | None = "026_knowledge_doc_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "incidents",
        "tenant_id",
        type_=sa.String(length=128),
        existing_type=sa.String(),
        existing_nullable=True,
    )
    op.alter_column(
        "llm_credentials",
        "tenant_id",
        type_=sa.String(length=128),
        existing_type=sa.String(),
        existing_nullable=True,
    )
    # ORM: Incident.__table_args__ -> ix_incidents_tenant_created. Nunca foi
    # criado por migration; IF NOT EXISTS protege bancos que ja o tenham.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_incidents_tenant_created "
        "ON incidents (tenant_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_incidents_tenant_created")
    op.alter_column(
        "llm_credentials",
        "tenant_id",
        type_=sa.String(),
        existing_type=sa.String(length=128),
        existing_nullable=True,
    )
    op.alter_column(
        "incidents",
        "tenant_id",
        type_=sa.String(),
        existing_type=sa.String(length=128),
        existing_nullable=True,
    )
