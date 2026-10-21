"""Testes unitarios do ABExperimentRepository (DA-85, candidata).

O endpoint de experimentos ainda nao existe, mas o repositorio e' codigo de
produto e estava em 0% de cobertura (IIC-AUD-036).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.ab_testing_orm import ABExperiment
from app.services.ab_test_repository import ABExperimentRepository

AGORA = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
HORA = timedelta(hours=1)


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


def test_default_is_active_do_orm_igual_ao_do_repositorio():
    """ORM e repositorio concordam: experimento nasce desligado."""
    assert ABExperiment.__table__.c.is_active.default.arg is False


@pytest.mark.asyncio
async def test_get_active_respeita_janela_de_datas(repo):
    await _novo(repo, name="sem-datas", is_active=True)
    await _novo(repo, name="ja-comecou", is_active=True, starting_at=AGORA - HORA)
    await _novo(
        repo,
        name="dentro-da-janela",
        is_active=True,
        starting_at=AGORA - HORA,
        ending_at=AGORA + HORA,
    )
    await _novo(repo, name="vai-comecar", is_active=True, starting_at=AGORA + HORA)
    await _novo(repo, name="ja-terminou", is_active=True, ending_at=AGORA - HORA)
    await _novo(repo, name="termina-agora", is_active=True, ending_at=AGORA)
    await _novo(repo, name="desligado", is_active=False, starting_at=AGORA - HORA)

    ativos = {e.name for e in await repo.get_active(now=AGORA)}

    assert ativos == {"sem-datas", "ja-comecou", "dentro-da-janela"}


@pytest.mark.asyncio
async def test_get_active_sem_now_usa_relogio_atual(repo):
    await _novo(repo, name="passado", is_active=True, starting_at=datetime.now(UTC) - HORA)
    await _novo(repo, name="futuro", is_active=True, starting_at=datetime.now(UTC) + HORA)

    assert [e.name for e in await repo.get_active()] == ["passado"]


@pytest.mark.asyncio
async def test_list_experiments_filtra_flag_e_ordena_mais_recente_primeiro(repo):
    for nome, ativo in (("primeiro", True), ("segundo", False), ("terceiro", True)):
        await _novo(repo, name=nome, is_active=ativo)
        await asyncio.sleep(0.002)  # created_at distinto para a ordenacao

    todos = await repo.list_experiments(include_inactive=True)
    assert [e.name for e in todos] == ["terceiro", "segundo", "primeiro"]

    # So' o flag: um ativo agendado para o futuro tambem aparece aqui.
    await _novo(repo, name="agendado", is_active=True, starting_at=AGORA + HORA * 1000)
    so_ativos = await repo.list_experiments()
    assert [e.name for e in so_ativos] == ["agendado", "terceiro", "primeiro"]


@pytest.mark.asyncio
async def test_update_respeita_allowlist_e_atualiza_timestamp(repo):
    experimento = await _novo(repo)
    antes = experimento.updated_at
    await asyncio.sleep(0.002)

    atualizado = await repo.update_experiment(
        experimento.experiment_id,
        {
            "name": "novo-nome",
            "is_active": True,
            "starting_at": AGORA - HORA,
            "experiment_id": "nao-pode-mudar",
            "ignorado": 1,
        },
    )

    assert atualizado is experimento
    assert atualizado.name == "novo-nome"
    assert atualizado.experiment_id != "nao-pode-mudar"
    assert not hasattr(atualizado, "ignorado")
    assert atualizado.updated_at > antes
    assert [e.name for e in await repo.get_active(now=AGORA)] == ["novo-nome"]
    assert await repo.update_experiment("nao-existe", {"name": "x"}) is None


@pytest.mark.asyncio
async def test_delete(repo):
    experimento = await _novo(repo)

    assert await repo.delete_experiment(experimento.experiment_id) is True
    assert await repo.get_by_id(experimento.experiment_id) is None
    assert await repo.delete_experiment(experimento.experiment_id) is False
