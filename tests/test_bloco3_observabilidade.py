"""Validacao 2026-10-07, Bloco 3: metering, metricas e dashboards (M-11, M-14, M-17)."""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.llm.origins import canonical_origin

# ---------------------------------------------------------------------------
# M-11: uma unica forma de origem; preco do registro; UPSERT atomico
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bruto", "canonico"),
    [
        ("api.groq.com", "https://api.groq.com"),
        ("https://API.groq.com/openai/v1", "https://api.groq.com"),
        ("127.0.0.1:11434", "http://127.0.0.1:11434"),
        ("http://ollama:11434", "http://ollama:11434"),
        ("https://x.openai.azure.com:443", "https://x.openai.azure.com"),
        ("local_lab", ""),
        ("ollama", ""),
    ],
)
def test_m11_canonical_origin(bruto, canonico):
    assert canonical_origin(bruto) == canonico


def test_m11_metering_grava_sob_a_origem_e_usa_preco_do_registro(monkeypatch):
    from app.admin import metering, repository

    gravado = {}
    monkeypatch.setattr(
        repository, "record_usage", lambda origin, model, **kw: gravado.update(o=origin, **kw)
    )
    monkeypatch.setattr(repository, "registry_prices", lambda o, m: (1.0, 2.0))
    cb = metering.UsageCaptureCallback("openai", "gpt-x", origin="https://api.groq.com")
    cb.tokens_in, cb.tokens_out, cb.requests, cb.has_usage = 1_000_000, 1_000_000, 1, True
    metering.record_usage_observed(cb, successful=True)
    assert gravado["o"] == "https://api.groq.com"
    assert gravado["cost_usd"] == 3.0  # 1M x $1 + 1M x $2 (tabela interna daria $20)


def test_m11_admin_recusa_origem_que_nao_e_origem():
    from fastapi import HTTPException
    from pydantic import ValidationError

    from app.admin.routes import ModelCreate, _origin_or_422

    with pytest.raises(ValidationError):
        ModelCreate(provider_origin="local_lab", model_id="m")
    assert ModelCreate(provider_origin="api.groq.com", model_id="m").provider_origin == (
        "https://api.groq.com"
    )
    with pytest.raises(HTTPException) as exc:
        _origin_or_422("ollama")
    assert exc.value.status_code == 422


@pytest.mark.skipif(
    not os.environ.get("IIC_TEST_DATABASE_URL"), reason="precisa de PostgreSQL descartavel"
)
def test_m11_upsert_atomico_sob_concorrencia(monkeypatch):
    from sqlalchemy import text

    from app.admin import repository
    from app.db import get_sync_session_factory, reset_sync_session_factory

    url = os.environ["IIC_TEST_DATABASE_URL"]
    subprocess.run(
        [".venv/bin/alembic", "upgrade", "head"],
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        check=True,
    )
    monkeypatch.setattr(repository.settings, "database_url", url)
    monkeypatch.setattr(repository.settings, "metering_enabled", True)
    reset_sync_session_factory()
    with get_sync_session_factory()() as s:
        s.execute(text("DELETE FROM llm_usage WHERE model_id = 'm11-conc'"))
        s.commit()

    def grava(_):
        return repository.record_usage("api.groq.com", "m11-conc", tokens_in=1)

    with ThreadPoolExecutor(16) as ex:
        assert all(ex.map(grava, range(200)))
    with get_sync_session_factory()() as s:
        total, linhas = s.execute(
            text("SELECT sum(tokens_in), count(*) FROM llm_usage WHERE model_id = 'm11-conc'")
        ).one()
        s.execute(text("DELETE FROM llm_usage WHERE model_id = 'm11-conc'"))
        s.commit()
    assert (total, linhas) == (200, 1)


# ---------------------------------------------------------------------------
# M-14: /metrics montado, com token; metricas com ponto de incremento
# ---------------------------------------------------------------------------


def test_m14_faixa_de_evidencia_e_label_discreto():
    from app import metrics

    assert [metrics.evidence_level(v) for v in (None, 0.1, 0.5, 0.9)] == [
        "none",
        "low",
        "medium",
        "high",
    ]
    if metrics._PROMETHEUS_AVAILABLE:
        assert "evidence_strength" not in metrics.DIAGNOSIS_TOTAL._labelnames


def test_m14_observe_diagnosis_incrementa_e_nunca_levanta():
    pytest.importorskip("prometheus_client")
    from app import metrics

    def valor(metric, **labels):
        return metric.labels(**labels)._value.get()

    antes = valor(metrics.PII_DETECTED_TOTAL, sensitivity_level="confidential")
    metrics.observe_diagnosis(
        agent_domain="sap",
        llm_provider=None,
        evidence_strength=0.83,
        latency_seconds=1.2,
        sensitivity_level="confidential",
        pii_detected=True,
    )
    assert valor(metrics.PII_DETECTED_TOTAL, sensitivity_level="confidential") == antes + 1
    assert (
        valor(
            metrics.DIAGNOSIS_TOTAL, agent_domain="sap", llm_provider="none", evidence_level="high"
        )
        >= 1
    )


_METRICS_PROBE = (
    "from fastapi.testclient import TestClient; from app.main import app; c=TestClient(app); "
    "print(c.get('/metrics').status_code, "
    "c.get('/metrics', headers={'Authorization': 'Bearer tok-m14'}).status_code)"
)


@pytest.mark.parametrize(("token", "esperado"), [("tok-m14", "401 200"), ("", "404 404")])
def test_m14_metrics_exige_token(token, esperado):
    pytest.importorskip("prometheus_fastapi_instrumentator")
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    saida = (
        subprocess.run(
            [os.sys.executable, "-c", _METRICS_PROBE],
            env={
                **os.environ,
                "PROMETHEUS_ENABLED": "true",
                "METRICS_TOKEN": token,
                "PYTHONPATH": raiz,
            },
            cwd=raiz,
            capture_output=True,
            text=True,
            timeout=180,
            check=True,
        )
        .stdout.strip()
        .splitlines()[-1]
    )
    assert saida == esperado
