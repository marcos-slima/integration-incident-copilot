"""fix evidence_strength column type String->Float (A-05)

Revision ID: 002
Revises: 001
Create Date: 2026-09-24

A-05 (auditoria 2026-09-24): evidence_strength foi definida como String(32)
na migration inicial, mas o campo correspondente em DiagnosisResponse
(app/models.py) e float|None. Isso impede agregacoes numericas nos
dashboards Grafana e queries de percentil no PostgreSQL.

Esta migration converte a coluna para FLOAT usando USING CAST para
preservar linhas existentes. Se existirem valores textuais legados
(ex: 'high', 'medium') que nao sejam numericos, eles serao convertidos
para NULL pela expressao USING.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Converte String(32) -> FLOAT; linhas com texto nao numerico viram NULL
    op.alter_column(
        "incidents",
        "evidence_strength",
        existing_type=sa.String(32),
        type_=sa.Float(),
        postgresql_using="evidence_strength::double precision",
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "incidents",
        "evidence_strength",
        existing_type=sa.Float(),
        type_=sa.String(32),
        nullable=True,
    )
