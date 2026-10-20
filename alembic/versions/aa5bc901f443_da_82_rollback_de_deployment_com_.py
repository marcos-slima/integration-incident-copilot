"""DA-82: rollback de deployment com snapshot
Revision ID: aa5bc901f443
Revises: 021
Create Date: 2026-10-19 15:50:48.895398

- snapshot_table: versao de schema antes de deploy (timestamp, schema_hash, operation)
- rollback_log: log de rollbacks realizados (timestamp, schema_hash, user_id, status)

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "aa5bc901f443"
down_revision: str | Sequence[str] | None = "021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "snapshot_table",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_hash", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_snapshot_table_created", "snapshot_table", ["created_at"])
    op.create_table(
        "rollback_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_schema_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rollback_log_created", "rollback_log", ["created_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_rollback_log_created", table_name="rollback_log")
    op.drop_table("rollback_log")
    op.drop_index("ix_snapshot_table_created", table_name="snapshot_table")
    op.drop_table("snapshot_table")
