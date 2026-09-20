"""Testes da camada HTTP (FastAPI)."""

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.main import app
from app.models import DiagnosisResponse

client = TestClient(app)


def _stub_diagnosis(request):
    return DiagnosisResponse(
        probable_root_cause="Causa raiz de teste (stub)",
        confidence=0.75,
        next_steps=["Passo 1"],
        report_markdown="## Diagnostico\n\nCausa raiz de teste (stub)",
        matched_source="doc_teste.md",
    )


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.integration
def test_diagnose_endpoint_known_case():
    response = client.post("/diagnose", json={"description": "iFlow falhando com erro 401"})
    assert response.status_code == 200
    body = response.json()
    assert body["matched_source"] == "cpi_http_401.md"
    assert body["confidence"] >= 0.5
    assert "report_markdown" in body


@pytest.mark.integration
def test_diagnose_endpoint_rejects_oversized_description():
    huge_description = "x" * 10_000
    response = client.post("/diagnose", json={"description": huge_description})
    assert response.status_code == 422


def test_diagnose_requires_api_key_when_configured(monkeypatch):
    """DA-18: com API_KEY configurada (seja via .env, seja porque
    _ensure_api_keys_configured gerou uma no startup real), /diagnose
    deve exigir X-API-Key valido - sem header, com header errado, e
    so entao com o header correto."""
    monkeypatch.setattr(main_module, "settings", Settings(api_key="secret-diagnose"))
    monkeypatch.setattr(main_module, "run_diagnosis", _stub_diagnosis)
    payload = {"description": "iFlow falhando com erro 401"}

    unauthorized = client.post("/diagnose", json=payload)
    assert unauthorized.status_code == 401

    wrong_key = client.post("/diagnose", json=payload, headers={"X-API-Key": "chave-errada"})
    assert wrong_key.status_code == 401

    authorized = client.post("/diagnose", json=payload, headers={"X-API-Key": "secret-diagnose"})
    assert authorized.status_code == 200
    assert authorized.json()["matched_source"] == "doc_teste.md"


def test_ensure_api_keys_configured_generates_missing_keys(monkeypatch):
    """DA-18: se API_KEY/A2A_API_KEY nao vierem do .env, o startup deve
    gerar uma chave aleatoria por processo para cada uma - nenhum dos
    dois endpoints protegidos pode ficar sem NENHUMA chave em memoria."""
    fresh_settings = Settings(api_key="", a2a_api_key="")
    monkeypatch.setattr(main_module, "settings", fresh_settings)

    main_module._ensure_api_keys_configured()

    assert fresh_settings.api_key != ""
    assert fresh_settings.a2a_api_key != ""
    assert fresh_settings.api_key != fresh_settings.a2a_api_key


def test_ensure_api_keys_configured_preserves_configured_keys(monkeypatch):
    """DA-18: se o operador ja configurou API_KEY/A2A_API_KEY no .env,
    o startup nao deve sobrescrever - senao a chave documentada/usada
    pelo operador pararia de funcionar a cada restart."""
    configured_settings = Settings(api_key="ja-configurada", a2a_api_key="tambem-configurada")
    monkeypatch.setattr(main_module, "settings", configured_settings)

    main_module._ensure_api_keys_configured()

    assert configured_settings.api_key == "ja-configurada"
    assert configured_settings.a2a_api_key == "tambem-configurada"
