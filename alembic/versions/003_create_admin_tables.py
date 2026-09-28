"""DA-46/47/48: tabelas admin — llm_models, llm_credentials, llm_usage

Revision ID: 003
Revises: 002
Create Date: 2026-09-28

Registro de modelos (DA-46), credenciais cifradas com Fernet (DA-47) e
metering de tokens reais (DA-48). Opt-in: so existem no schema quando
DATABASE_URL e configurada e `alembic upgrade head` e aplicado — o
runtime (sem LLM_REGISTRY_DB) nao lê estas tabelas em nenhum caminho
(default e .env puro, Fase A preserva o "clone e rode" sem banco).

Percentual consumido e calculado por query (app/admin/repository.py:
`usage_summary`), nao armazenado: tokens_total / monthly_limit_tokens.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers
revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_models",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("provider_origin", sa.String(128), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("base_url", sa.String(256), nullable=True),
        sa.Column("price_in_per_1m", sa.Float(), nullable=True),
        sa.Column("price_out_per_1m", sa.Float(), nullable=True),
        sa.Column("monthly_limit_tokens", sa.BigInteger(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("provider_origin", "model_id", name="uq_llm_models_origin_model"),
    )
    op.create_index("ix_llm_models_provider_origin", "llm_models", ["provider_origin"])

    op.create_table(
        "llm_credentials",
        sa.Column("provider_origin", sa.String(128), primary_key=True),
        sa.Column("encrypted_key", sa.Text(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("masked", sa.String(16), nullable=False),
        sa.Column("last_test_status", sa.String(32), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "llm_usage",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("provider_origin", sa.String(128), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column(
            "period_start", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tokens_in", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_out", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("requests", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failures", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_llm_usage_provider_origin", "llm_usage", ["provider_origin"])
    op.create_index("ix_llm_usage_origin_created", "llm_usage", ["provider_origin", "period_start"])
    # Um unico periodo aberto por (origem, modelo): em PostgreSQL os NULLs
    # sao distintos num UNIQUE normal, por isso o indice parcial.
    op.create_index(
        "uq_llm_usage_open",
        "llm_usage",
        ["provider_origin", "model_id"],
        unique=True,
        postgresql_where=sa.text("period_end IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_llm_usage_open", table_name="llm_usage")
    op.drop_index("ix_llm_usage_origin_created", table_name="llm_usage")
    op.drop_index("ix_llm_usage_provider_origin", table_name="llm_usage")
    op.drop_table("llm_usage")
    op.drop_table("llm_credentials")
    op.drop_index("ix_llm_models_provider_origin", table_name="llm_models")
    op.drop_table("llm_models")
