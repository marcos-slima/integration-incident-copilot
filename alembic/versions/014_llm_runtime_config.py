"""M-18: tabela llm_runtime_config (overrides de runtime via admin UI).

Revision ID: 014
Revises: 013
Create Date: 2026-10-16

Permite que o administrador troque LLM_REGISTRY_DB, LLM_PROVIDER e
LLM_ROUTE sem tocar .env nem fazer deploy. Uma unica linha (id=1).

Sem DATABASE_URL a tabela nao existe; resolve_registry_config() cai no
comportamento de sempre (.env) — fail-closed: sem banco, sem override.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_runtime_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("llm_registry_db", sa.Boolean(), nullable=False, server_default="f"),
        sa.Column("llm_provider", sa.String(32), nullable=True),
        sa.Column("llm_route", sa.String(64), nullable=True),
    )
    # Linha inicial (id=1) com valores default
    op.execute(
        "INSERT INTO llm_runtime_config (id, llm_registry_db, llm_provider, llm_route) "
        "VALUES (1, false, NULL, NULL)"
    )


def downgrade() -> None:
    op.drop_table("llm_runtime_config")
