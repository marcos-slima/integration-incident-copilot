"""B-01: persistencia de diagnosticos na tabela incidents
(app/services/incident_recorder.py) - best-effort, sem quebrar o diagnostico."""

import uuid

from app.connectors.base import ConnectorResult
from app.models import DiagnosisResponse, IncidentRequest
from app.services import incident_recorder


def _response(**overrides):
    data = {
        "probable_root_cause": "certificado expirado",
        "model_confidence": 0.8,
        "diagnosis_confidence": 0.6,
        "next_steps": [],
        "report_markdown": "",
        "evidence_strength": 0.75,
        "llm_provider_used": "ollama",
        "agent_domain": "sap",
    }
    data.update(overrides)
    return DiagnosisResponse(**data)


def _connector(is_mock=False):
    return ConnectorResult(
        source_system="OData",
        status="error",
        error_code="HTTP_401",
        message="m",
        raw="",
        is_mock=is_mock,
    )


def test_build_row_uses_diagnosis_id_and_redacts_description():
    incident_id = str(uuid.uuid4())
    row = incident_recorder.build_incident_row(
        incident_id=incident_id,
        request=IncidentRequest(
            description="falha no envio para joao@empresa.com", interface_type="odata"
        ),
        response=_response(),
        final_state={"connector_data": _connector()},
        latency_ms=1234,
    )
    assert row["id"] == uuid.UUID(incident_id)
    assert "joao@empresa.com" not in row["description"]
    assert row["pii_detected"] is True
    assert row["redaction_applied"] is True
    assert row["connector_source_system"] == "OData"
    assert row["is_mock"] is False
    assert row["sensitivity_level"] == "confidential"
    assert row["error_codes"] == ["HTTP_401"]
    assert row["evidence_strength"] == 0.75
    assert row["latency_ms"] == 1234


def test_record_incident_is_noop_without_database_url(monkeypatch):
    monkeypatch.setattr(incident_recorder.settings, "database_url", "")

    def _must_not_connect():
        raise AssertionError("nao deveria abrir conexao sem DATABASE_URL")

    monkeypatch.setattr(incident_recorder, "_get_session_factory", _must_not_connect)
    incident_recorder.record_incident(incident_id=str(uuid.uuid4()))


def test_record_incident_swallows_database_errors(monkeypatch, caplog):
    monkeypatch.setattr(incident_recorder.settings, "database_url", "postgresql://x/y")

    def _db_down():
        raise ConnectionError("postgres fora do ar")

    monkeypatch.setattr(incident_recorder, "_get_session_factory", _db_down)
    incident_recorder.record_incident(
        incident_id=str(uuid.uuid4()),
        request=IncidentRequest(description="x"),
        response=_response(),
        final_state={},
        latency_ms=1,
    )
    assert "Falha ao gravar incidente" in caplog.text


def test_record_incident_adds_and_commits(monkeypatch):
    monkeypatch.setattr(incident_recorder.settings, "database_url", "postgresql://x/y")
    added = []

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def add(self, obj):
            added.append(obj)

        def commit(self):
            added.append("commit")

    monkeypatch.setattr(incident_recorder, "_get_session_factory", lambda: _Session)
    incident_id = str(uuid.uuid4())
    incident_recorder.record_incident(
        incident_id=incident_id,
        request=IncidentRequest(description="x"),
        response=_response(),
        final_state={},
        latency_ms=1,
    )
    assert str(added[0].id) == incident_id
    assert added[1] == "commit"


def test_sync_url_uses_psycopg2_driver():
    assert (
        incident_recorder._sync_url("postgresql+asyncpg://u:p@h/db")
        == "postgresql+psycopg2://u:p@h/db"
    )
    assert incident_recorder._sync_url("postgres://u:p@h/db") == "postgresql+psycopg2://u:p@h/db"
