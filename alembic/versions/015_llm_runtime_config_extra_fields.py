"""M-19: colunas extras na tabela llm_runtime_config.

Revision ID: 015
Revises: 014
Create Date: 2026-10-16

Adiciona quatro campos que a auditoria de GAPs operacionais identificou como
necessarios para administracao via UI (sem editar .env):

    llm_credentials_master_key   — chave Fernet para cifrar credenciais no
                                   registro (GAP critico: bloqueia o CRUD
                                   de credenciais quando ausente)
    data_sovereignty_mode        — politica de soberania de dados ("strict"
                                   ou "cloud_with_dlp")
    confidential_allowed_origins — lista de origens permitidas para dados
                                   confidenciais (separada por virgula)
    require_auth                 — exige chave API para subir o processo

    rule_engine_enabled          — liga/desliga o motor de regras de erros
                                   conhecidos (DA-33)

Todas as colunas sao nullable/sem server_default: o valor do .env e' a fonte
de verdade quando nao preenchido. O admin UI compara DB vs .env no banner.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None

# Colunas que estao sendo adicionadas — usadas pelo ORM e pelas rotas.
_EXTRA_COLUMNS = (
    "llm_credentials_master_key",
    "data_sovereignty_mode",
    "confidential_allowed_origins",
    "require_auth",
    "rule_engine_enabled",
)


def upgrade() -> None:
    op.add_column(
        "llm_runtime_config",
        sa.Column(
            "llm_credentials_master_key",
            sa.String(512),
            nullable=True,
        ),
    )
    op.add_column(
        "llm_runtime_config",
        sa.Column(
            "data_sovereignty_mode",
            sa.String(32),
            nullable=True,
        ),
    )
    op.add_column(
        "llm_runtime_config",
        sa.Column(
            "confidential_allowed_origins",
            sa.String(512),
            nullable=True,
        ),
    )
    op.add_column(
        "llm_runtime_config",
        sa.Column(
            "require_auth",
            sa.Boolean(),
            nullable=True,
        ),
    )
    op.add_column(
        "llm_runtime_config",
        sa.Column(
            "rule_engine_enabled",
            sa.Boolean(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    # Remover em ordem inversa (PostgreSQL exige).
    op.drop_column("llm_runtime_config", "rule_engine_enabled")
    op.drop_column("llm_runtime_config", "require_auth")
    op.drop_column("llm_runtime_config", "confidential_allowed_origins")
    op.drop_column("llm_runtime_config", "data_sovereignty_mode")
    op.drop_column("llm_runtime_config", "llm_credentials_master_key")
