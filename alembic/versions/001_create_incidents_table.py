"""Cria tabela incidents (Fase 1 Observabilidade Grafana).

Revision ID: 001
Revises:
Create Date: 2026-09-24

Schema decisions:
- UUID primary key: evita colisão em shards / réplicas futuras
- JSONB para evidence_json e error_codes: queries analíticas no Grafana
  sem precisar de tabelas auxiliares (ex: WHERE evidence_json @> '{"type":"IDoc"}')
- DateTime(timezone=True): timestamps em UTC explícito — crítico para
  dashboards multi-timezone do Grafana
- TimescaleDB opt-in: esta migration cria a tabela PG pura; para habilitar
  TimescaleDB, execute após upgrade:
      SELECT create_hypertable('incidents', 'created_at');
      SELECT add_retention_policy('incidents', INTERVAL '2 years');
- Índices compostos: agent_domain+created_at (COI), llm_provider+created_at
  (iPaaS), sensitivity_level+created_at (SOC)
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        # Chave primária
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            default=uuid.uuid4,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # Rastreabilidade
        sa.Column("trace_id", sa.String(128), nullable=True),
        # Entrada
        sa.Column("interface_type", sa.String(64), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("connector_source_system", sa.String(128), nullable=True),
        sa.Column("is_mock", sa.Boolean, nullable=False, server_default="false"),
        # Resultado diagnóstico
        sa.Column("probable_root_cause", sa.Text, nullable=True),
        sa.Column("model_confidence", sa.Float, nullable=True),
        sa.Column("diagnosis_confidence", sa.Float, nullable=True),
        sa.Column("evidence_strength", sa.String(32), nullable=True),
        sa.Column("llm_provider_used", sa.String(64), nullable=True),
        sa.Column("agent_domain", sa.String(64), nullable=True),
        sa.Column("evidence_json", postgresql.JSONB, nullable=True),
        # Segurança / SOC
        sa.Column("sensitivity_level", sa.String(32), nullable=True),
        sa.Column("pii_detected", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("redaction_applied", sa.Boolean, nullable=False, server_default="false"),
        # Execução
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("error_codes", postgresql.JSONB, nullable=True),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Verificação humana
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("diagnosis_correct", sa.Boolean, nullable=True),
        sa.Column("verified_by", sa.String(128), nullable=True),
        sa.Column("verified_root_cause", sa.Text, nullable=True),
    )

    # Índices simples
    op.create_index("ix_incidents_trace_id", "incidents", ["trace_id"])
    op.create_index("ix_incidents_created_at", "incidents", ["created_at"])

    # Índices compostos para queries analíticas dos dashboards Grafana
    op.create_index(
        "ix_incidents_agent_created", "incidents", ["agent_domain", "created_at"]
    )
    op.create_index(
        "ix_incidents_provider_created", "incidents", ["llm_provider_used", "created_at"]
    )
    op.create_index(
        "ix_incidents_sensitivity", "incidents", ["sensitivity_level", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_incidents_sensitivity", "incidents")
    op.drop_index("ix_incidents_provider_created", "incidents")
    op.drop_index("ix_incidents_agent_created", "incidents")
    op.drop_index("ix_incidents_created_at", "incidents")
    op.drop_index("ix_incidents_trace_id", "incidents")
    op.drop_table("incidents")
