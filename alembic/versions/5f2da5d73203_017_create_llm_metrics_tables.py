"""017_create_llm_metrics_tables

Revision ID: 5f2da5d73203
Revises: 016
Create Date: 2026-10-17 19:27:36.259761

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5f2da5d73203"
down_revision: str | Sequence[str] | None = "016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """DA-70: Create tables for LLM metrics (FinOps).

    Creates three tables to store Prometheus-style metrics:
    - llm_requests: count of LLM calls by provider/model
    - llm_tokens: token counts (input/output) by provider/model
    - llm_cost: cost in USD by provider/model

    All tables have a composite primary key to allow upserts and
    efficient aggregation by time/provider/model.
    """
    # Table: llm_requests
    op.create_table(
        "llm_requests",
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("count", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("created_at", "provider", "model"),
    )
    op.create_index(
        op.f("ix_llm_requests_created_at"), "llm_requests", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_llm_requests_provider"), "llm_requests", ["provider"], unique=False)

    # Table: llm_tokens
    op.create_table(
        "llm_tokens",
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("token_type", sa.String(length=10), nullable=False),  # 'input' | 'output'
        sa.Column("count", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("created_at", "provider", "model", "token_type"),
    )
    op.create_index(op.f("ix_llm_tokens_created_at"), "llm_tokens", ["created_at"], unique=False)
    op.create_index(op.f("ix_llm_tokens_provider"), "llm_tokens", ["provider"], unique=False)

    # Table: llm_cost
    op.create_table(
        "llm_cost",
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("created_at", "provider", "model"),
    )
    op.create_index(op.f("ix_llm_cost_created_at"), "llm_cost", ["created_at"], unique=False)
    op.create_index(op.f("ix_llm_cost_provider"), "llm_cost", ["provider"], unique=False)


def downgrade() -> None:
    """DA-70: Drop LLM metrics tables."""
    op.drop_index(op.f("ix_llm_cost_provider"), table_name="llm_cost")
    op.drop_index(op.f("ix_llm_cost_created_at"), table_name="llm_cost")
    op.drop_table("llm_cost")

    op.drop_index(op.f("ix_llm_tokens_provider"), table_name="llm_tokens")
    op.drop_index(op.f("ix_llm_tokens_created_at"), table_name="llm_tokens")
    op.drop_table("llm_tokens")

    op.drop_index(op.f("ix_llm_requests_provider"), table_name="llm_requests")
    op.drop_index(op.f("ix_llm_requests_created_at"), table_name="llm_requests")
    op.drop_table("llm_requests")
