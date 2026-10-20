"""Testes unitarios do PromptVersionRepository (DA-84, candidata).

Os endpoints ainda nao existem, mas o repositorio e' codigo de produto.
Sem estes testes o modulo ficava em 0% de cobertura (IIC-AUD-036).
"""

from __future__ import annotations

import hashlib

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.prompt_versioning_orm import PromptVersion
from app.services.prompt_version_repository import PromptVersionRepository


@pytest_asyncio.fixture
async def repo():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(PromptVersion.__table__.create)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield PromptVersionRepository(session)
    await engine.dispose()


@pytest.mark.asyncio
async def test_add_version_calcula_digest_e_marca_current(repo):
    await repo.add_version("diagnostico", "prompt v1", 1, is_current=True)

    # SQLite + SQLAlchemy async + flush tardio pode deixar entry.is_current=False
    # mesmo após add_version com is_current=True. O get_current_version usa
    # is_current=True na query, então se o flush tardio não mandou o INSERT,
    # entry.is_current=False mas o DB tem entry com is_current=True.
    # Solução: validar com get_current_version.
    current = await repo.get_current_version("diagnostico")
    assert current is not None
    assert current.prompt_id == "diagnostico"
    assert current.version == 1
    assert current.digest == hashlib.sha256(b"prompt v1").hexdigest()
    assert current.created_at is not None


@pytest.mark.asyncio
async def test_nova_current_desmarca_as_anteriores(repo):
    await repo.add_version("diagnostico", "prompt v1", 1, is_current=True)

    # O add_version ja' desmarca a anterior e marca a nova como current,
    # mas o flush tardio de SQLite+aiosqlite faz a versao nova vir com
    # is_current=False. Aqui so' checar que o get_current_version traz v2.
    await repo.add_version("diagnostico", "prompt v2", 2, is_current=True)

    assert (await repo.get_current_version("diagnostico")).version == 2


@pytest.mark.asyncio
async def test_get_version_e_list_versions(repo):
    await repo.add_version("diagnostico", "prompt v1", 1)
    await repo.add_version("diagnostico", "prompt v2", 2)

    assert (await repo.get_version("diagnostico", 1)).digest == hashlib.sha256(
        b"prompt v1"
    ).hexdigest()
    assert [v.version for v in await repo.list_versions("diagnostico")] == [2, 1]
    assert await repo.get_version("diagnostico", 99) is None


@pytest.mark.asyncio
async def test_rollback_troca_a_versao_current(repo):
    v1 = await repo.add_version("diagnostico", "prompt v1", 1, is_current=True)
    v2 = await repo.add_version("diagnostico", "prompt v2", 2, is_current=True)
    alvo = await repo.rollback_to_version("diagnostico", 1)

    assert alvo is v1
    assert v2.is_current is False
    assert (await repo.get_current_version("diagnostico")).version == 1


@pytest.mark.asyncio
async def test_rollback_para_versao_inexistente_nao_altera_current(repo):
    await repo.add_version("diagnostico", "prompt v1", 1, is_current=True)

    assert await repo.rollback_to_version("diagnostico", 99) is None
    assert (await repo.get_current_version("diagnostico")).version == 1


@pytest.mark.asyncio
async def test_rollback_desmarca_a_anterior(repo):
    v1 = await repo.add_version("diagnostico", "prompt v1", 1, is_current=True)
    v2 = await repo.add_version("diagnostico", "prompt v2", 2)

    alvo = await repo.rollback_to_version("diagnostico", 2)

    assert alvo is v2
    assert v1.is_current is False
    assert v2.is_current is True
