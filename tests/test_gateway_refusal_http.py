"""DA-66/DA-67 na borda HTTP: recusa previsivel do AI Gateway (orcamento
esgotado, policy de soberania) vira 503 com `code` estavel e error_id - nao
o 500 "erro interno" do handler generico. A mensagem da excecao (origens e
motivos por candidato) fica so no log."""

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.llm.budget import BudgetExhaustedError
from app.llm.gateway import PolicyViolationError

client = TestClient(main_module.app, raise_server_exceptions=False)
_INTERNAL = "http://ollama-interno.svc.cluster.local:11434"


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (BudgetExhaustedError(f"restante $0.0010 origem {_INTERNAL}"), "budget_exhausted"),
        (
            PolicyViolationError(f"nenhum provider permitido primario@{_INTERNAL}"),
            "policy_violation",
        ),
    ],
)
def test_gateway_refusal_is_503_with_code(monkeypatch, caplog, exc, code):
    def _raise(request):
        raise exc

    monkeypatch.setattr(main_module, "run_diagnosis", _raise)
    r = client.post("/diagnose", json={"description": "interface parada desde ontem"})

    assert r.status_code == 503
    body = r.json()
    assert body["code"] == code
    assert len(body["error_id"]) == 32
    assert _INTERNAL not in r.text  # origem interna nao vaza ao cliente
    assert "Retry-After" not in r.headers
    assert body["error_id"] in caplog.text and _INTERNAL in caplog.text


def test_unhandled_error_keeps_500_with_code(monkeypatch):
    def _raise(request):
        raise RuntimeError("segredo interno")

    monkeypatch.setattr(main_module, "run_diagnosis", _raise)
    r = client.post("/diagnose", json={"description": "interface parada desde ontem"})
    assert r.status_code == 500
    assert r.json()["code"] == "internal_error"
    assert "segredo" not in r.text


def test_timeout_and_overload_carry_code(monkeypatch):
    from app.exceptions import DiagnosisOverloadedError, DiagnosisTimeoutError

    for exc, status, code in (
        (DiagnosisTimeoutError("excedeu 180s"), 504, "timeout"),
        (DiagnosisOverloadedError("capacidade esgotada"), 503, "overloaded"),
    ):

        def _raise(request, _e=exc):
            raise _e

        monkeypatch.setattr(main_module, "run_diagnosis", _raise)
        r = client.post("/diagnose", json={"description": "interface parada desde ontem"})
        assert (r.status_code, r.json()["code"]) == (status, code)
