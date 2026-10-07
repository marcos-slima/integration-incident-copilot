"""SEC-03: limite de tentativas do codigo de telefone (web_users).

Revision ID: 010
Revises: 009
Create Date: 2026-10-07

O codigo de 6 digitos (10 min) nao tinha contador: 5/min por IP no
/auth/verify/phone, mas com IPs diferentes o espaco de 10^6 codigos podia ser
varrido. `phone_code_attempts` conta os erros do codigo vigente; ao atingir
`settings.phone_code_max_attempts` o codigo e invalidado e o admin precisa
reemitir (app/webusers.py::confirm_phone).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "web_users",
        sa.Column("phone_code_attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("web_users", "phone_code_attempts")
