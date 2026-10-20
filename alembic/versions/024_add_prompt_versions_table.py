"""Create prompt_versions table (DA-84 prompt versioning).

Revision ID: 024_add_prompt_versions_table
Revises: 023_add_tenant_id_credentials
Create Date: 2026-10-19

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "024_add_prompt_versions_table"
down_revision: str | None = "023_add_tenant_id_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("prompt_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prompt_id", "version", name="uq_prompt_versions_prompt_version"),
    )
    op.create_index(
        op.f("ix_prompt_versions_prompt_id"), "prompt_versions", ["prompt_id"], unique=False
    )
    # Indice unico PARCIAL: apenas uma versao is_current=true por prompt.
    # UniqueConstraint nao aceita postgresql_where no SQLAlchemy atual.
    op.create_index(
        "uq_prompt_versions_current",
        "prompt_versions",
        ["prompt_id", "is_current"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )


def downgrade() -> None:
    op.drop_index("uq_prompt_versions_current", table_name="prompt_versions")
    op.drop_index(op.f("ix_prompt_versions_prompt_id"), table_name="prompt_versions")
    op.drop_table("prompt_versions")
