"""Alembic env.py — configuração do ambiente de migrations.

Integra com o app/config.py (pydantic-settings) para obter
DATABASE_URL do .env, sem hard-code na alembic.ini.

Suporte a:
- Modo offline (gera SQL sem conectar — útil para revisão)
- Modo online síncrono (psycopg2 — usado pelo CLI `alembic upgrade head`)
- Auto-detecção de schema via SQLAlchemy metadata (autogenerate)

TimescaleDB: após `alembic upgrade head`, execute manualmente:
    SELECT create_hypertable('incidents', 'created_at');
    SELECT add_retention_policy('incidents', INTERVAL '2 years');
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Garante que o pacote `app` é importável a partir do root do projeto
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402

# Importar modelos para que o metadata os registre (autogenerate)
from app.services.incident_repository import Incident  # noqa: E402, F401

# ---------------------------------------------------------------------------
# Configuração Alembic
# ---------------------------------------------------------------------------

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Injetar DATABASE_URL do settings (sync — psycopg2 para migrations CLI)
_raw_url = settings.database_url or ""
if _raw_url:
    # Alembic CLI usa psycopg2 (sync) — garantir dialeto correto
    _sync_url = (
        _raw_url
        .replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgres+asyncpg://", "postgresql://")
    )
    config.set_main_option("sqlalchemy.url", _sync_url)

target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_migrations_offline() -> None:
    """Gera SQL sem abrir conexão real (modo offline)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Executa migrations conectando ao banco (modo online, CLI normal)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
