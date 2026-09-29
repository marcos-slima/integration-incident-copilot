"""DA-52: migration 005 - baseline de contrato por sistema integrado

Revision ID: 005
Revises: 004
Create Date: 2026-09-28

`system_contracts` guarda o contrato normalizado observado de cada sistema
integrado (app/contracts/), nao um flag de "mudou". Uma linha por
observacao; o baseline e a linha de maior `observed_at`.

Decisoes que valem para quem ler o proximo diff:

- SEM FK para `integration_systems`. O detector tambem roda contra sistemas
  que so existem no .env; perder a observacao por causa de FK seria perder
  justamente o sinal que indica que o cadastro do catalogo esta errado.
- SEM unique em `system_key`. A restriction e' (system_key, observed_at):
  duas observacoes do mesmo sistema em horarios diferentes sao o historico,
  nao uma violacao.
- `contract` e' JSONB porque e' escrito e relido por codigo. Um dia o
  diff precisara recalcular de um baseline antigo, e JSONB permite; um
  relacional nao.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers
revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_contracts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("system_key", sa.String(64), nullable=False),
        sa.Column("connector_type", sa.String(32), nullable=False),
        sa.Column("contract_kind", sa.String(32), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("contract", postgresql.JSONB, nullable=False),
        sa.Column("entity_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("property_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("observation_status", sa.String(32), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("notes", sa.Text, nullable=True),
        sa.UniqueConstraint("system_key", "observed_at", name="uq_system_contracts_key_observed"),
    )
    op.create_index(
        "ix_system_contracts_key_observed", "system_contracts", ["system_key", "observed_at"]
    )
    op.create_index("ix_system_contracts_system_key", "system_contracts", ["system_key"])
    op.create_index("ix_system_contracts_fingerprint", "system_contracts", ["fingerprint"])
    op.create_index("ix_system_contracts_observed_at", "system_contracts", ["observed_at"])


def downgrade() -> None:
    op.drop_table("system_contracts")
