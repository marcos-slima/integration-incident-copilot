"""DA-74: idioma do diagnostico e dos e-mails de ativacao

Revision ID: 021
Revises: 020
Create Date: 2026-10-18

- incidents.locale: idioma em que o diagnostico foi produzido (pt-BR | en).
- web_users.locale: idioma das mensagens de ativacao (e-mail e SMS).

Ambas anulaveis: linhas anteriores nao registram idioma.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "021"
down_revision: str | Sequence[str] | None = "020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("locale", sa.String(length=8), nullable=True))
    op.add_column("web_users", sa.Column("locale", sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column("web_users", "locale")
    op.drop_column("incidents", "locale")
