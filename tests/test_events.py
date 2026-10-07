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
    "specversion": "1.0",
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
        model_confidence=0.7,
        diagnosis_confidence=0.0,
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
    # §3.4: webhook responde 202 Accepted sem corpo; diagnostico roda em background.
    assert ok.status_code == 202


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


def test_failed_background_diagnosis_releases_event_id(monkeypatch):
    """Falha no diagnostico (DLQ) libera o cloudevents.id - a reentrega
    do mesmo evento precisa ser aceita, nao descartada como duplicata."""
    from fastapi import BackgroundTasks

    from app.events import idempotency

    def _boom(request):
        raise RuntimeError("LLM fora do ar")

    monkeypatch.setattr(consumer_module, "run_diagnosis", _boom)
    envelope = IncidentEventEnvelope(**_VALID_PAYLOAD)

    tasks = BackgroundTasks()
    consumer_module.handle_incident_event_async(envelope, tasks)
    assert len(tasks.tasks) == 1
    consumer_module._run_diagnosis_background(envelope)

    assert idempotency.is_duplicate(envelope.id) is False


def test_duplicate_event_is_not_scheduled_twice():
    from fastapi import BackgroundTasks

    envelope = IncidentEventEnvelope(**_VALID_PAYLOAD)
    tasks = BackgroundTasks()
    consumer_module.handle_incident_event_async(envelope, tasks)
    consumer_module.handle_incident_event_async(envelope, tasks)
    assert len(tasks.tasks) == 1


def test_webhook_enqueues_durably_and_returns_job_id_when_redis_configured(monkeypatch):
    """B-02: com REDIS_URL, 202 so depois do enqueue no RQ; corpo traz job_id."""
    import app.queue as queue_module

    monkeypatch.setattr("app.main.settings", Settings(event_mesh_api_key="secret-event"))
    monkeypatch.setattr("app.config.settings.redis_url", "redis://fake:6379/0")
    enqueued = []
    monkeypatch.setattr(
        queue_module, "enqueue_incident_event", lambda data: enqueued.append(data) or "job-123"
    )

    resp = client.post(
        "/events/incident", json=_VALID_PAYLOAD, headers={"X-Event-Mesh-Api-Key": "secret-event"}
    )
    assert resp.status_code == 202
    assert resp.json() == {"status": "queued", "job_id": "job-123"}
    assert enqueued[0]["id"] == "evt-1"


def test_webhook_returns_503_when_enqueue_fails(monkeypatch):
    """B-02: sem enqueue durable confirmado nao ha 202 - publicador reenvia."""
    import app.queue as queue_module

    monkeypatch.setattr("app.main.settings", Settings(event_mesh_api_key="secret-event"))
    monkeypatch.setattr("app.config.settings.redis_url", "redis://fake:6379/0")

    def _redis_down(data):
        raise ConnectionError("redis fora do ar")

    monkeypatch.setattr(queue_module, "enqueue_incident_event", _redis_down)
    resp = client.post(
        "/events/incident", json=_VALID_PAYLOAD, headers={"X-Event-Mesh-Api-Key": "secret-event"}
    )
    assert resp.status_code == 503


def test_run_event_job_dedups_completes_and_releases_on_failure(monkeypatch):
    """B-02/B-03: worker faz claim -> done; falha libera o id para o retry do RQ."""
    import pytest

    import app.agent.graph as graph_module
    from app.events import idempotency
    from app.queue import run_event_job

    calls = []

    def _flaky(request):
        calls.append(request.description)
        if len(calls) == 1:
            raise RuntimeError("LLM fora do ar")
        return _stub_diagnosis(request)

    monkeypatch.setattr(graph_module, "run_diagnosis", _flaky)
    data = IncidentEventEnvelope(**_VALID_PAYLOAD).model_dump(mode="json")

    with pytest.raises(RuntimeError):
        run_event_job(data)
    assert idempotency.is_duplicate("evt-1") is False  # liberado na falha
    idempotency.release("evt-1")

    result = run_event_job(data)  # retry do RQ
    assert result["probable_root_cause"] == "causa para iFlow falhando com erro 401"
    assert run_event_job(data) == {"status": "duplicate", "cloudevents_id": "evt-1"}
    assert len(calls) == 2
