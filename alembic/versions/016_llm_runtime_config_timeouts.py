"""M-20: colunas de timeout na tabela llm_runtime_config.

Revision ID: 016
Revises: 015
Create Date: 2026-10-16

Adiciona dois campos de timeout que a auditoria de GAPs operacionais identificou
como necessarios para administracao via UI (sem editar .env):

    diagnosis_timeout_seconds        — timeout do grafo de diagnostico (segundos)
    llm_request_timeout_seconds      — timeout de chamada LLM (segundos)

Ambos sao nullable/sem server_default: o valor do .env e' a fonte de verdade
quando nao preenchido. O admin UI compara DB vs .env no banner.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_runtime_config",
        sa.Column(
            "diagnosis_timeout_seconds",
            sa.Float(),
            nullable=True,
        ),
    )
    op.add_column(
        "llm_runtime_config",
        sa.Column(
            "llm_request_timeout_seconds",
            sa.Float(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    # Remover em ordem inversa (PostgreSQL exige).
    op.drop_column("llm_runtime_config", "llm_request_timeout_seconds")
    op.drop_column("llm_runtime_config", "diagnosis_timeout_seconds")
