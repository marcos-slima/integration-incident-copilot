"""Validacao 2026-10-07, Bloco 3: entrada e erros (M-05, M-15, M-16)."""

from __future__ import annotations

import pytest

from app.connectors.base import validate_identifier_charset


@pytest.mark.parametrize("ident", ["abc\n", "..", "a..b", ".", "...", "../etc", "a/b", "x y", ""])
def test_m05_identificadores_recusados(ident):
    assert validate_identifier_charset(ident, "X") is not None


@pytest.mark.parametrize("ident", ["CPI-401-DEMO", "RFC-IDOC-51-DEMO", "1.0.3", "PO_4500012345"])
def test_m05_identificadores_legitimos(ident):
    assert validate_identifier_charset(ident, "X") is None


# ---------------------------------------------------------------------------
# M-15: 422 sem eco do valor recebido; 500 com error_id
# ---------------------------------------------------------------------------


def test_m15_422_nao_ecoa_o_input(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "api_key", "k" * 32)
    segredo = "cpf 123.456.789-09 joao@empresa.com " + "x" * 60_000
    r = TestClient(app).post(
        "/diagnose", json={"description": segredo}, headers={"X-API-Key": "k" * 32}
    )
    assert r.status_code == 422
    assert "123.456.789-09" not in r.text and "joao@empresa.com" not in r.text
    assert r.json()["detail"][0]["loc"] == ["body", "description"]


def test_m15_500_tem_error_id_e_nao_vaza_detalhe(monkeypatch, caplog):
    from fastapi.testclient import TestClient

    from app import main
    from app.config import settings

    monkeypatch.setattr(settings, "api_key", "k" * 32)

    def _quebra(*a, **k):
        raise RuntimeError("senha=XYZ no stack interno")

    monkeypatch.setattr(main, "run_diagnosis", _quebra)
    client = TestClient(main.app, raise_server_exceptions=False)
    r = client.post("/diagnose", json={"description": "teste"}, headers={"X-API-Key": "k" * 32})
    assert r.status_code == 500
    corpo = r.json()
    assert corpo["detail"] == "erro interno" and len(corpo["error_id"]) == 32
    assert "XYZ" not in r.text
    assert corpo["error_id"] in caplog.text


# ---------------------------------------------------------------------------
# M-16: CloudEvents 1.0 estrito e dedup por (source, id)
# ---------------------------------------------------------------------------

_DATA = {"description": "IDoc 51"}


@pytest.mark.parametrize("falta", ["specversion", "id", "source"])
def test_m16_atributos_obrigatorios(falta):
    from pydantic import ValidationError

    from app.models import IncidentEventEnvelope

    evento = {
        "specversion": "1.0",
        "type": "com.sap.integration.incident.detected.v1",
        "source": "/sap/cpi",
        "id": "1",
        "data": _DATA,
    }
    evento.pop(falta)
    with pytest.raises(ValidationError):
        IncidentEventEnvelope(**evento)


def test_m16_mesmo_id_em_origens_diferentes_nao_e_duplicata():
    from app.events import idempotency
    from app.models import IncidentEventEnvelope

    base = {"specversion": "1.0", "type": "com.sap.integration.incident.detected.v1", "data": _DATA}
    a = IncidentEventEnvelope(**base, source="/sap/cpi", id="evt-m16")
    b = IncidentEventEnvelope(**base, source="/sap/solman", id="evt-m16")
    assert idempotency.event_key(a) != idempotency.event_key(b)
    assert idempotency.is_duplicate(idempotency.event_key(a)) is False
    assert idempotency.is_duplicate(idempotency.event_key(b)) is False
    assert idempotency.is_duplicate(idempotency.event_key(a)) is True
