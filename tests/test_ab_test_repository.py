"""Testes unitarios do ABExperimentRepository (DA-85, candidata).

O endpoint de experimentos ainda nao existe, mas o repositorio e' codigo de
produto e estava em 0% de cobertura (IIC-AUD-036).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.ab_testing_orm import ABExperiment
from app.services.ab_test_repository import ABExperimentRepository


@pytest_asyncio.fixture
async def repo():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(ABExperiment.__table__.create)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield ABExperimentRepository(session)
    await engine.dispose()


def _novo(repo, **overrides):
    dados = {
        "name": "prompt-v2",
        "description": "teste A/B",
        "variant_a_prompt": "v1",
        "variant_b_prompt": "v2",
    }
    dados.update(overrides)
    return repo.create_experiment(**dados)


@pytest.mark.asyncio
async def test_create_e_busca_por_id(repo):
    experimento = await _novo(repo)

    assert experimento.experiment_id
    assert experimento.name == "prompt-v2"
    assert experimento.is_active is False
    assert experimento.created_at is not None

    assert (await repo.get_by_id(experimento.experiment_id)) is experimento
    assert await repo.get_by_id("nao-existe") is None


@pytest.mark.asyncio
async def test_lista_ativos_e_inclui_inativos(repo):
    # SQLite com SQLAlchemy async exige flush explicito para manter
    # o estado in-memory consistente com o banco
    await _novo(repo, name="ativo", is_active=True)
    await repo._session.flush()
    await _novo(repo, name="inativo", is_active=False)
    await repo._session.flush()
    await _novo(repo, name="agendado", is_active=True, starting_at=datetime.now(UTC))
    await repo._session.flush()

    ativos = await repo.get_active()
    assert len(ativos) == 1

    todos = await repo.list_experiments(include_inactive=True)
    assert {e.name for e in todos} == {"ativo", "inativo", "agendado"}

    so_ativos = await repo.list_experiments()
    assert {e.name for e in so_ativos} == {"ativo", "agendado"}


@pytest.mark.asyncio
async def test_update_respeita_allowlist(repo):
    experimento = await _novo(repo)

    atualizado = await repo.update_experiment(
        experimento.experiment_id,
        {"name": "novo-nome", "experiment_id": "nao-pode-mudar", "ignorado": 1},
    )

    assert atualizado is experimento
    assert atualizado.name == "novo-nome"
    assert atualizado.experiment_id != "nao-pode-mudar"
    assert await repo.update_experiment("nao-existe", {"name": "x"}) is None


@pytest.mark.asyncio
async def test_delete(repo):
    experimento = await _novo(repo)

    assert await repo.delete_experiment(experimento.experiment_id) is True
    assert await repo.get_by_id(experimento.experiment_id) is None
    assert await repo.delete_experiment(experimento.experiment_id) is False
