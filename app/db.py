"""Camada de banco de dados — engine SQLAlchemy assíncrono.

Design opt-in (mesmo padrão de REDIS_URL e GRAPH_RAG_ENABLED):
- DATABASE_URL vazia (default) → nenhuma conexão é aberta, nenhum
  import de asyncpg acontece — o app sobe normalmente sem PostgreSQL.
- DATABASE_URL preenchida → engine async + sessão criados no lifespan
  do FastAPI (app/main.py), disponibilizando o IncidentRepository.

URL aceita dois formatos:
  postgresql+asyncpg://user:pass@host:5432/dbname   (async, default)
  postgresql://user:pass@host:5432/dbname           (convertido auto)

Uso:
    from app.db import AsyncSessionLocal, engine, is_db_enabled

    async with AsyncSessionLocal() as session:
        ...

Migrations (Alembic):
    alembic upgrade head     # aplica todas as migrations
    alembic downgrade -1     # reverte última migration
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Normalização da URL
# ---------------------------------------------------------------------------


def _async_url(raw: str) -> str:
    """Garante dialeto asyncpg independente de como o operador configurou."""
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+asyncpg://", 1)
    return raw


def _sync_url(raw: str) -> str:
    """Dialeto sync (psycopg2) a partir da mesma URL configurada no .env.

    O pipeline e' async (asyncpg), mas o incident_recorder, o admin e o
    detector de drift (DA-52) rodam em caminho SINCRONO por construcao:
    sao chamado de dentro de `run_diagnosis`, que e' sync e roda em
    threadpool. Traduzir a URL num lugar so evita que cada um invente o
    proprio `_sync_url` e um deles esqueça um prefixo.
    """
    for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
        if raw.startswith(prefix):
            return "postgresql+psycopg2://" + raw[len(prefix) :]
    return raw


#: Cache do sessionmaker sync: `(url_configurada, sessionmaker)`. Tupla em
#: vez de `lru_cache` de zero argumentos porque o resultado depende de
#: `settings.database_url`, que e' mutavel. Um `lru_cache()` sem chave
#: guardaria o primeiro resultado para sempre: uma chamada com a URL vazia
#: devolveria `None` e o `None` ficaria em cache mesmo depois da URL ser
#: configurada -- falha silenciosa, sem excecao, sem log.
_sync_factory: tuple[str, Any] | None = None


def get_sync_session_factory():
    """Sessionmaker sync compartilhado, ou `None` sem `DATABASE_URL`.

    Reconstrói quando a `DATABASE_URL` muda, e NÃO cacheia o `None`: sem
    banco configurado não há pool a preservar, e devolver `None` sempre
    permite que a configuracao apareca depois (import tardio de settings,
    teste, reconfiguracao em runtime).
    """
    global _sync_factory

    url = getattr(settings, "database_url", "")
    if not url:
        return None
    if _sync_factory is not None and _sync_factory[0] == url:
        return _sync_factory[1]

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        _sync_url(url),
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=3,
        connect_args={"connect_timeout": 3},
    )
    factory = sessionmaker(engine, expire_on_commit=False)
    _sync_factory = (url, factory)
    return factory


def reset_sync_session_factory() -> None:
    """Esquece o engine em cache (usado por testes que trocam a URL)."""
    global _sync_factory
    if _sync_factory is not None:
        _sync_factory[1].kw["bind"].dispose()
        _sync_factory = None


def is_db_enabled() -> bool:
    """Retorna True se DATABASE_URL estiver configurada."""
    return bool(getattr(settings, "database_url", ""))


# ---------------------------------------------------------------------------
# Engine e session factory (None se DB não configurado)
# ---------------------------------------------------------------------------

engine = None
AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None

if is_db_enabled():
    _url = _async_url(settings.database_url)  # type: ignore[attr-defined]
    engine = create_async_engine(
        _url,
        echo=False,  # True em dev se quiser ver SQL no log
        pool_pre_ping=True,  # detecta conexões mortas antes de usar
        pool_size=5,
        max_overflow=10,
    )
    AsyncSessionLocal = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    logger.info("PostgreSQL engine criado: %s", _url.split("@")[-1])
else:
    logger.debug("DATABASE_URL não configurada — persistência de incidentes desabilitada.")


# ---------------------------------------------------------------------------
# Base declarativa ORM
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Base para todos os modelos SQLAlchemy deste projeto."""


# ---------------------------------------------------------------------------
# Dependency FastAPI / helper de sessão
# ---------------------------------------------------------------------------


async def get_db_session() -> AsyncGenerator[AsyncSession | None, None]:
    """FastAPI Dependency que fornece sessão assíncrona (ou None se DB off).

    Uso em endpoints:
        @router.get("/")
        async def handler(session: AsyncSession | None = Depends(get_db_session)):
            if session is None:
                return {"error": "Persistência não configurada"}
    """
    if AsyncSessionLocal is None:
        yield None
        return
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
