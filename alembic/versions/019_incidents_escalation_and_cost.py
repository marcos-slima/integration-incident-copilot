"""incidents: sinal de escalonamento (DA-44) e custo do diagnostico (DA-67)

Revision ID: 019
Revises: 018
Create Date: 2026-10-18

Antes, o sinal de escalonamento so ia para a resposta e para o log, e o
custo por diagnostico so existia no ledger (DA-67) quando o metering mediu
tokens. Com estas colunas a Admin UI lista o que pede revisao humana e
quanto cada diagnostico debitou do orcamento. Anulaveis: linhas anteriores
a 019 nao tem o dado, e um default fabricaria historico.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "019"
down_revision: str | Sequence[str] | None = "018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("should_escalate", sa.Boolean(), nullable=True))
    op.add_column("incidents", sa.Column("escalation_reason", sa.String(length=32), nullable=True))
    op.add_column("incidents", sa.Column("cost_usd", sa.Float(), nullable=True))
    op.add_column("incidents", sa.Column("llm_calls", sa.Integer(), nullable=True))
    op.create_index("ix_incidents_escalate_created", "incidents", ["should_escalate", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_incidents_escalate_created", table_name="incidents")
    op.drop_column("incidents", "llm_calls")
    op.drop_column("incidents", "cost_usd")
    op.drop_column("incidents", "escalation_reason")
    op.drop_column("incidents", "should_escalate")
