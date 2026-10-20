"""Create knowledge_document_versions table (DA-86 UI versionamento RAG).

Revision ID: 026_add_knowledge_document_versions_table
Revises: 025_add_ab_experiments_table
Create Date: 2026-10-19

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "026_add_knowledge_document_versions_table"
down_revision: str | None = "025_add_ab_experiments_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_document_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "version", name="uq_knowledge_document_version"),
        sa.UniqueConstraint(
            "document_id",
            "is_current",
            name="uq_knowledge_document_current",
            postgresql_where="is_current = true",
        ),
    )
    op.create_index(
        op.f("ix_knowledge_document_versions_document_id"),
        "knowledge_document_versions",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_document_versions_is_current"),
        "knowledge_document_versions",
        ["is_current"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_knowledge_document_versions_is_current"), table_name="knowledge_document_versions"
    )
    op.drop_index(
        op.f("ix_knowledge_document_versions_document_id"), table_name="knowledge_document_versions"
    )
    op.drop_table("knowledge_document_versions")
