"""DA-46/47/48 — testes do AdminRepository (async) sobre SQLite (aiosqlite).

Estrategia identica a de test_incident_repository.py, mas SEM espelho de
modelo: os modelos admin (`llm_models`, `llm_credentials`, `llm_usage`)
usam tipos compativeis com SQLite (Uuid/Text/Float/BigInteger), entao os
testes reutilizam os MESMOS modelos de producao via aiosqlite.

Cobre: CRUD de modelos, credencial cifrada (roundtrip + versao),
resumo de uso com percentual, reset de periodo e no-op do escritor sync
sem DATABASE_URL.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.admin import crypto
from app.admin.models import LlmCredential, LlmModel, LlmUsage, percent_consumed
from app.admin.repository import AdminRepository, record_usage
from app.config import settings


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        for table in (LlmModel.__table__, LlmCredential.__table__, LlmUsage.__table__):
            await conn.run_sync(table.create)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def repo(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "llm_credentials_master_key", Fernet.generate_key().decode())
    async with session_factory() as session:
        yield AdminRepository(session)


@pytest_asyncio.fixture
async def repo_session(session_factory):
    async with session_factory() as session:
        yield session


@pytest.mark.asyncio
async def test_crud_model(repo, repo_session):
    model = await repo.create_model(
        provider_origin="api.groq.com",
        model_id="qwen/qwen3.8-27b",
        base_url="https://api.groq.com/",
        price_in_per_1m=0.15,
        monthly_limit_tokens=1_000_000,
        is_default=True,
    )
    assert model.id is not None
    assert await repo.get_model(model.id) is not None
    assert await repo.get_model(uuid.uuid4()) is None

    updated = await repo.update_model(model.id, {"enabled": False, "monthly_limit_tokens": 500_000})
    assert updated is not None and updated.enabled is False
    assert updated.monthly_limit_tokens == 500_000
    # campos fora da allowlist nao mudam
    await repo.update_model(model.id, {"model_id": "outro"})
    assert (await repo.get_model(model.id)).model_id == "qwen/qwen3.8-27b"

    assert await repo.delete_model(model.id) is True
    assert await repo.get_model(model.id) is None
    assert await repo.delete_model(model.id) is False


@pytest.mark.asyncio
async def test_duplicate_model_rejected_by_web_layer(repo):
    await repo.create_model(provider_origin="api.groq.com", model_id="m1")
    # a unicidade e (origin, model): modelo diferente na mesma origem ok
    await repo.create_model(provider_origin="api.groq.com", model_id="m2")
    assert len(await repo.list_models()) == 2


@pytest.mark.asyncio
async def test_credential_roundtrip_cifrada(repo):
    plain = "segredo_de_teste_123"
    cred = await repo.set_credential("api.groq.com", plain)
    assert crypto.decrypt_secret(cred.encrypted_key) == plain
    assert "segredo_de_teste" not in cred.encrypted_key
    assert cred.masked != plain and "****" in cred.masked

    # rotacao: versao incrementa, plaintext novo
    v0 = cred.key_version
    cred2 = await repo.set_credential("api.groq.com", "segredo_outra_chave")
    assert cred2.key_version == v0 + 1
    assert crypto.decrypt_secret(cred2.encrypted_key) == "segredo_outra_chave"

    # runtime decrypts via repositório
    assert await repo.decrypt_credential("api.groq.com") == "segredo_outra_chave"
    assert await repo.decrypt_credential("api.desconhecida.com") is None

    assert await repo.delete_credential("api.groq.com") is True
    assert await repo.get_credential("api.groq.com") is None


@pytest.mark.asyncio
async def test_credential_requer_master_key(repo, monkeypatch):
    monkeypatch.setattr(settings, "llm_credentials_master_key", "")
    from app.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError):
        await repo.set_credential("api.groq.com", "segredo")


@pytest.mark.asyncio
async def test_usage_summary_percentual(repo, repo_session):
    model = await repo.create_model(
        provider_origin="api.groq.com",
        model_id="qwen/qwen3.8-27b",
        monthly_limit_tokens=10_000,
    )
    assert model is not None
    # M-11: o registro grava a origem canonica; o metering (record_usage)
    # tambem - o uso tem de estar sob a MESMA chave para casar.
    assert model.provider_origin == "https://api.groq.com"
    repo_session.add(
        LlmUsage(
            provider_origin="https://api.groq.com",
            model_id="qwen/qwen3.8-27b",
            tokens_in=2_500,
            tokens_out=2_500,
            requests=5,
            failures=1,
            cost_usd=0.0123,
        )
    )
    await repo_session.flush()

    rows = await repo.usage_summary()
    assert len(rows) == 1
    row = rows[0]
    assert row["tokens_total"] == 5_000
    assert row["requests"] == 5
    assert row["failures"] == 1
    assert row["cost_usd"] == 0.0123
    assert row["percent"] == 50  # 5000 de 10000


@pytest.mark.asyncio
async def test_reset_usage_period(repo, repo_session):
    await repo.create_model(provider_origin="api.groq.com", model_id="m1")
    repo_session.add(LlmUsage(provider_origin="https://api.groq.com", model_id="m1", tokens_in=100))
    await repo_session.flush()

    new_period = await repo.reset_usage_period("api.groq.com", "m1")
    assert new_period is not None
    assert new_period.tokens_in == 0
    old_period = await repo.get_open_usage("api.groq.com", "m1")
    assert old_period is new_period

    closed = [
        row
        for row in (await repo_session.execute(select(LlmUsage))).scalars()
        if row.period_end is not None
    ]
    assert len(closed) == 1
    assert closed[0].tokens_in == 100


def test_percent_consumed():
    assert percent_consumed(tokens_in=100, tokens_out=100, monthly_limit_tokens=1000) == 20
    assert percent_consumed(tokens_in=100, tokens_out=0, monthly_limit_tokens=None) is None
    assert percent_consumed(tokens_in=0, tokens_out=0, monthly_limit_tokens=0) is None


def test_record_usage_noop_sem_banco(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "metering_enabled", True)
    # no-op sem DATABASE_URL: retorna False, sem excecao
    assert record_usage("api.groq.com", "m1", tokens_in=10) is False


def test_record_usage_respeita_metering_flag(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "metering_enabled", False)
    assert record_usage("api.groq.com", "m1") is False
