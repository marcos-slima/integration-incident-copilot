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


def test_verify_incident_returns_404_when_graph_rag_disabled(monkeypatch):
    monkeypatch.setattr(main_module, "settings", Settings(graph_rag_enabled=False))
    response = client.post(
        "/incidents/i1/verify",
        json={"root_cause": "causa confirmada", "verified_by": "human"},
    )
    assert response.status_code == 404
    assert "desligado" in response.json()["detail"]


def test_verify_incident_returns_404_when_incident_not_found(monkeypatch):
    monkeypatch.setattr(main_module, "settings", Settings(graph_rag_enabled=True))
    monkeypatch.setattr(main_module, "verify_incident", lambda **kwargs: False)
    response = client.post(
        "/incidents/inexistente/verify",
        json={"root_cause": "causa confirmada", "verified_by": "human"},
    )
    assert response.status_code == 404
    assert "inexistente" in response.json()["detail"]


def test_verify_incident_returns_200_on_success(monkeypatch):
    monkeypatch.setattr(main_module, "settings", Settings(graph_rag_enabled=True))
    captured = {}

    def _fake_verify_incident(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(main_module, "verify_incident", _fake_verify_incident)
    response = client.post(
        "/incidents/i1/verify",
        json={"root_cause": "causa confirmada por Basis", "verified_by": "human"},
    )
    assert response.status_code == 200
    assert response.json() == {"incident_id": "i1", "status": "verified"}
    assert captured["incident_id"] == "i1"
    assert captured["verified_root_cause"] == "causa confirmada por Basis"
    assert captured["verified_by"] == "human"


def test_verify_incident_defaults_verified_by_to_human(monkeypatch):
    monkeypatch.setattr(main_module, "settings", Settings(graph_rag_enabled=True))
    captured = {}

    def _fake_verify_incident(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(main_module, "verify_incident", _fake_verify_incident)
    response = client.post("/incidents/i1/verify", json={"root_cause": "causa confirmada"})
    assert response.status_code == 200
    assert captured["verified_by"] == "human"


def test_verify_incident_requires_api_key_when_configured(monkeypatch):
    """Mesma politica de autenticacao de /diagnose (DA-18) - X-API-Key
    tambem protege este endpoint mutante."""
    monkeypatch.setattr(
        main_module, "settings", Settings(api_key="secret-verify", graph_rag_enabled=True)
    )
    monkeypatch.setattr(main_module, "verify_incident", lambda **kwargs: True)
    payload = {"root_cause": "causa confirmada"}

    unauthorized = client.post("/incidents/i1/verify", json=payload)
    assert unauthorized.status_code == 401

    authorized = client.post(
        "/incidents/i1/verify", json=payload, headers={"X-API-Key": "secret-verify"}
    )
    assert authorized.status_code == 200
