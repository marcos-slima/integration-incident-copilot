"""M-17: papel somente leitura para o Grafana.

Revision ID: 013
Revises: 012
Create Date: 2026-10-07

O datasource do Grafana usava o usuario DONO do banco (`iic`): um painel ou
uma conta Grafana comprometida podia apagar `incidents` ou ler/alterar
`llm_credentials`. Esta migration cria `iic_grafana_ro` com SELECT nas
tabelas que os dashboards leem - e so nelas (nada de llm_credentials,
web_users ou system_contracts).

A senha vem de GRAFANA_DB_PASSWORD no ambiente de quem roda a migration.
Sem ela, a migration nao faz nada (aviso no log) e o Grafana continua com o
usuario do .env - o compose aceita os dois (ver docker-compose.yml).
"""

from __future__ import annotations

import logging
import os

from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

ROLE = "iic_grafana_ro"
# Tabelas lidas pelos 4 dashboards (scripts/validate_dashboards.py executa as 45 queries).
TABLES = ("incidents", "integration_systems")


def upgrade() -> None:
    password = os.environ.get("GRAFANA_DB_PASSWORD", "")
    if not password:
        logger.warning(
            "M-17: GRAFANA_DB_PASSWORD ausente - papel %s NAO criado; o Grafana segue com o "
            "usuario do .env. Rode de novo com a variavel (alembic downgrade 012 && upgrade 013).",
            ROLE,
        )
        return
    can_create = (
        op.get_bind()
        .exec_driver_sql(
            "SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user"
        )
        .scalar()
    )
    if not can_create:
        logger.warning(
            "M-17: o usuario das migrations nao pode criar papeis (sem CREATEROLE) - "
            "crie %s manualmente com SELECT em %s.",
            ROLE,
            ", ".join(TABLES),
        )
        return
    literal = "'" + password.replace("'", "''") + "'"
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
                CREATE ROLE {ROLE} LOGIN PASSWORD {literal};
            ELSE
                ALTER ROLE {ROLE} LOGIN PASSWORD {literal};
            END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT CONNECT ON DATABASE {_db()} TO {ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
    for table in TABLES:
        op.execute(f"GRANT SELECT ON {table} TO {ROLE}")


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
                EXECUTE 'REVOKE ALL ON {", ".join(TABLES)} FROM {ROLE}';
                EXECUTE 'REVOKE USAGE ON SCHEMA public FROM {ROLE}';
                EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM {ROLE}', current_database());
                DROP ROLE {ROLE};
            END IF;
        END
        $$;
        """
    )


def _db() -> str:
    return '"' + op.get_bind().exec_driver_sql("SELECT current_database()").scalar() + '"'
