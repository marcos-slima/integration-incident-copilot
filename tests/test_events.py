"""DA-23 (Event Mesh): ingestao orientada a evento - POST
/events/incident recebe um envelope CloudEvents e dispara
run_diagnosis() automaticamente. Mocka run_diagnosis (mesmo padrao de
tests/test_api.py::_stub_diagnosis) - nao precisa de Ollama/Qdrant
reais, e nao e teste de integracao."""

from fastapi.testclient import TestClient

import app.events.consumer as consumer_module
from app.config import Settings
from app.events.consumer import handle_incident_event, to_incident_request
from app.main import app
from app.models import DiagnosisResponse, IncidentEventEnvelope

client = TestClient(app)

_VALID_PAYLOAD = {
    "type": "com.sap.integration.incident.detected.v1",
    "source": "cpi-monitor",
    "id": "evt-1",
    "time": "2026-09-20T00:00:00Z",
    "data": {
        "description": "iFlow falhando com erro 401",
        "interface_type": "odata",
        "identifier": "CPI-401-DEMO",
    },
}


def _stub_diagnosis(request):
    return DiagnosisResponse(
        probable_root_cause=f"causa para {request.description}",
        confidence=0.7,
        next_steps=["passo"],
        report_markdown="## ok",
        agent_domain="sap",
    )


def test_to_incident_request_maps_event_data_fields():
    envelope = IncidentEventEnvelope(**_VALID_PAYLOAD)
    request = to_incident_request(envelope)
    assert request.description == "iFlow falhando com erro 401"
    assert request.interface_type == "odata"
    assert request.identifier == "CPI-401-DEMO"


def test_handle_incident_event_calls_run_diagnosis(monkeypatch):
    monkeypatch.setattr(consumer_module, "run_diagnosis", _stub_diagnosis)
    envelope = IncidentEventEnvelope(**_VALID_PAYLOAD)
    result = handle_incident_event(envelope)
    assert result.probable_root_cause == "causa para iFlow falhando com erro 401"


def test_incident_event_webhook_requires_api_key_when_configured(monkeypatch):
    monkeypatch.setattr("app.main.settings", Settings(event_mesh_api_key="secret-event"))
    monkeypatch.setattr(consumer_module, "run_diagnosis", _stub_diagnosis)

    unauthorized = client.post("/events/incident", json=_VALID_PAYLOAD)
    assert unauthorized.status_code == 401

    wrong_key = client.post(
        "/events/incident", json=_VALID_PAYLOAD, headers={"X-Event-Mesh-Api-Key": "chave-errada"}
    )
    assert wrong_key.status_code == 401

    ok = client.post(
        "/events/incident", json=_VALID_PAYLOAD, headers={"X-Event-Mesh-Api-Key": "secret-event"}
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["agent_domain"] == "sap"
    assert body["probable_root_cause"] == "causa para iFlow falhando com erro 401"


def test_incident_event_webhook_rejects_unknown_event_type(monkeypatch):
    monkeypatch.setattr("app.main.settings", Settings(event_mesh_api_key="secret-event"))
    monkeypatch.setattr(consumer_module, "run_diagnosis", _stub_diagnosis)

    bad_payload = dict(_VALID_PAYLOAD, type="com.sap.integration.incident.resolved.v1")
    response = client.post(
        "/events/incident", json=bad_payload, headers={"X-Event-Mesh-Api-Key": "secret-event"}
    )
    assert response.status_code == 422


def test_incident_event_webhook_rejects_oversized_description(monkeypatch):
    monkeypatch.setattr("app.main.settings", Settings(event_mesh_api_key="secret-event"))
    monkeypatch.setattr(consumer_module, "run_diagnosis", _stub_diagnosis)

    huge_payload = {
        "type": "com.sap.integration.incident.detected.v1",
        "data": {"description": "x" * 10_000},
    }
    response = client.post(
        "/events/incident", json=huge_payload, headers={"X-Event-Mesh-Api-Key": "secret-event"}
    )
    assert response.status_code == 422


def test_ensure_api_keys_configured_generates_event_mesh_key(monkeypatch):
    import app.main as main_module

    settings = Settings(event_mesh_api_key="")
    monkeypatch.setattr(main_module, "settings", settings)
    main_module._ensure_api_keys_configured()
    assert settings.event_mesh_api_key != ""
