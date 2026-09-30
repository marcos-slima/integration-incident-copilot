"""DA-55: migration 007 — usuarios da UI web (web_users)

Revision ID: 007
Revises: 006
Create Date: 2026-09-29

Manutencao de usuarios pelo admin (CRUD em /admin/api/users + tela
/admin/users) com ativacao em DUAS etapas antes do login valer:
token por e-mail, depois codigo de 6 digitos por telefone.

Decisoes que valem para quem ler o proximo diff:

- `password_hash` e' PBKDF2 no MESMO formato da DA-54 (ponto como
  separador — `$` e' comido pela interpolacao do docker compose; ver
  DA-54). Nenhuma senha em claro, nunca.
- `phone_code_hash` guarda so' o HASH do codigo (sha256 com o segredo de
  sessao como salt server-side), nunca o codigo. Unico-uso e curto.
- SEM FK para outras tabelas: usuario da UI nao referencia modelo LLM,
  sistema integrado nem incidente.
- Compativel com PostgreSQL E SQLite de teste (padrao do admin desde
  DA-46): `Uuid` nativo-driver, sem JSONB.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers
revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "web_users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("username", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("email", sa.String(256), nullable=False, index=True),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, index=True),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("phone_code_hash", sa.String(128), nullable=True),
        sa.Column("phone_code_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("web_users")
