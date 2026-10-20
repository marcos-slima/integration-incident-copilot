"""DA-72: roteamento por registro de modelos

Revision ID: 020
Revises: 019
Create Date: 2026-10-18

- llm_models: parametros por modelo que o Policy Engine (DA-66) usa para
  decidir. Todos anulaveis (nulo = comportamento de hoje / derivado), menos
  `priority` (desempate, default 100).
- llm_runtime_config: orcamento e pesos de roteamento editaveis pela Admin UI.
- llm_call_latency: latencia por chamada bem-sucedida, base da mediana usada
  no score custo x latencia.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "020"
down_revision: str | Sequence[str] | None = "019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MODEL_COLS = (
    sa.Column("transport", sa.String(length=32), nullable=True),
    sa.Column("context_window_tokens", sa.Integer(), nullable=True),
    sa.Column("supports_tools", sa.Boolean(), nullable=True),
    sa.Column("supports_response_format", sa.Boolean(), nullable=True),
    sa.Column("supports_seed", sa.Boolean(), nullable=True),
    sa.Column("domains", sa.JSON(), nullable=True),
    sa.Column("tier", sa.String(length=16), nullable=True),
    sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
)
_RUNTIME_COLS = (
    "diagnosis_budget_usd",
    "llm_budget_reserve_ratio",
    "llm_gateway_max_cost_usd",
    "llm_routing_cost_weight",
    "llm_routing_latency_weight",
)


def upgrade() -> None:
    for col in _MODEL_COLS:
        op.add_column("llm_models", col)
    for name in _RUNTIME_COLS:
        op.add_column("llm_runtime_config", sa.Column(name, sa.Float(), nullable=True))
    op.create_table(
        "llm_call_latency",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_origin", sa.String(length=128), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_llm_call_latency_model_created",
        "llm_call_latency",
        ["provider_origin", "model_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_call_latency_model_created", table_name="llm_call_latency")
    op.drop_table("llm_call_latency")
    for name in reversed(_RUNTIME_COLS):
        op.drop_column("llm_runtime_config", name)
    for col in reversed(_MODEL_COLS):
        op.drop_column("llm_models", col.name)
