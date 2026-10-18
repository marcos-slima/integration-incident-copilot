"""DA-72: leituras sync do registro (app/admin/repository.py) contra SQLite."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.admin import repository as repo
from app.admin.models import (
    LlmCallLatency,
    LlmCredential,
    LlmModel,
    LlmRuntimeConfig,
    LlmUsage,
)
from app.db import Base


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    tables = [
        t.__table__ for t in (LlmModel, LlmUsage, LlmCallLatency, LlmCredential, LlmRuntimeConfig)
    ]
    Base.metadata.create_all(engine, tables=tables)
    factory = sessionmaker(engine)
    monkeypatch.setattr(repo.settings, "database_url", "sqlite://")
    monkeypatch.setattr(repo, "_get_sync_session_factory", lambda: factory)
    return factory


def test_load_registry_models_only_enabled_with_routing_fields(db):
    with db() as s:
        s.add_all(
            [
                LlmModel(
                    provider_origin="https://api.groq.com",
                    model_id="oss",
                    enabled=True,
                    transport="openai_compatible",
                    domains=["saas"],
                    tier="standard",
                    context_window_tokens=131072,
                    supports_tools=False,
                    priority=10,
                ),
                LlmModel(provider_origin="https://api.groq.com", model_id="off", enabled=False),
            ]
        )
        s.commit()
    rows = repo.load_registry_models()
    assert [r["model_id"] for r in rows] == ["oss"]
    r = rows[0]
    assert (r["transport"], r["domains"], r["tier"], r["priority"]) == (
        "openai_compatible",
        ["saas"],
        "standard",
        10,
    )
    assert r["supports_tools"] is False and r["supports_seed"] is None


def test_without_database_registry_is_unavailable(monkeypatch):
    monkeypatch.setattr(repo.settings, "database_url", "")
    assert repo.load_registry_models() is None  # None != [] : fail-closed no gateway


def test_open_usage_and_credentials(db):
    with db() as s:
        s.add(
            LlmUsage(
                provider_origin="https://api.openai.com",
                model_id="gpt-4o-mini",
                tokens_in=700,
                tokens_out=300,
            )
        )
        s.add(
            LlmUsage(
                provider_origin="https://api.openai.com",
                model_id="gpt-4o-mini",
                tokens_in=9999,
                tokens_out=0,
                period_end=datetime.now(UTC),
            )
        )
        s.add(
            LlmCredential(provider_origin="https://api.groq.com", encrypted_key="x", masked="****")
        )
        s.commit()
    assert repo.open_usage_tokens() == {("https://api.openai.com", "gpt-4o-mini"): 1000}
    assert repo.credentialed_origins() == {"https://api.groq.com"}


def test_latency_median_recent_successes(db):
    for ms in (100, 300, 200):
        assert repo.record_call_latency("api.groq.com", "oss", ms)
    with db() as s:  # amostra velha fica fora da janela de 7 dias
        s.add(
            LlmCallLatency(
                provider_origin="https://api.groq.com",
                model_id="oss",
                latency_ms=99999,
                created_at=datetime.now(UTC) - timedelta(days=30),
            )
        )
        s.commit()
    assert repo.latency_medians() == {("https://api.groq.com", "oss"): 200.0}


def test_runtime_config_keys_include_budget_and_weights(db):
    with db() as s:
        s.add(LlmRuntimeConfig(id=1, llm_registry_db=False))
        s.commit()
    out = repo.update_runtime_config(
        {"diagnosis_budget_usd": 2.0, "llm_routing_cost_weight": 0.5, "nao_existe": 1}
    )
    assert out["diagnosis_budget_usd"] == 2.0 and out["llm_routing_cost_weight"] == 0.5
    assert "nao_existe" not in out
    assert repo.get_runtime_config()["llm_routing_latency_weight"] is None
