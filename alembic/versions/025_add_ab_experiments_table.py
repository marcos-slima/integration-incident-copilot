"""Create ab_experiments table (DA-85 A/B testing).

Revision ID: 025_add_ab_experiments_table
Revises: 024_add_prompt_versions_table
Create Date: 2026-10-19

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "025_add_ab_experiments_table"
down_revision: str | None = "024_add_prompt_versions_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ab_experiments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("experiment_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("variant_a_prompt", sa.Text(), nullable=False),
        sa.Column("variant_b_prompt", sa.Text(), nullable=False),
        sa.Column("starting_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ending_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experiment_id", name="uq_ab_experiments_id"),
    )
    op.create_index(
        op.f("ix_ab_experiments_experiment_id"), "ab_experiments", ["experiment_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_ab_experiments_experiment_id"), table_name="ab_experiments")
    op.drop_table("ab_experiments")
