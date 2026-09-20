"""Testes do circuit breaker compartilhado pelos conectores HTTP
(app/connectors/base.py::circuit_breaker_guard). Usa o
ServiceNowConnector real (via httpx.MockTransport) como veiculo -
qualquer um dos 7 conectores HTTP serviria, ServiceNow e o mais simples
(sem passo de OAuth2 antes da chamada de dados).

Avaliacao externa (medio prazo, item 3): "Circuit breaker nos
conectores (ex.: tenacity + contador de falhas)".
"""

import httpx

from app.config import Settings
from app.connectors.base import ConnectorResult, circuit_breaker_guard, connector_circuit_breaker
from app.connectors.servicenow_connector import ServiceNowConnector


def _failing_client() -> tuple[httpx.Client, dict]:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("connection refused", request=request)

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_circuit_breaker_guard_is_none_when_closed():
    assert circuit_breaker_guard("ServiceNow-teste-fechado") is None


def test_circuit_breaker_guard_blocks_after_threshold_failures():
    connector_circuit_breaker.record_failure("X", failure_threshold=3)
    connector_circuit_breaker.record_failure("X", failure_threshold=3)
    assert connector_circuit_breaker.is_open("X", cooldown_seconds=30.0) is False

    connector_circuit_breaker.record_failure("X", failure_threshold=3)
    assert connector_circuit_breaker.is_open("X", cooldown_seconds=30.0) is True


def test_circuit_breaker_guard_returns_fallback_result_when_open():
    connector_circuit_breaker.record_failure("Y", failure_threshold=1)
    blocked = circuit_breaker_guard("Y")

    assert isinstance(blocked, ConnectorResult)
    assert blocked.error_code == "CIRCUIT_OPEN"
    assert blocked.is_fallback is True
    assert blocked.is_mock is False


def test_circuit_breaker_guard_recovers_after_success():
    connector_circuit_breaker.record_failure("Z", failure_threshold=1)
    assert connector_circuit_breaker.is_open("Z", cooldown_seconds=30.0) is True

    connector_circuit_breaker.record_success("Z")
    assert connector_circuit_breaker.is_open("Z", cooldown_seconds=30.0) is False


def test_servicenow_connector_opens_circuit_after_configured_failures_and_skips_network(
    monkeypatch,
):
    """N chamadas com erro de rede consecutivas abrem o circuito
    (N = connector_circuit_failure_threshold); a (N+1)-esima chamada
    nao deve nem tentar a rede - o handler do MockTransport prova isso
    contando quantas vezes foi de fato invocado."""
    client, calls = _failing_client()
    settings_override = Settings(
        servicenow_instance_url="https://demo.service-now.com",
        connector_circuit_failure_threshold=3,
        connector_circuit_cooldown_seconds=30.0,
    )
    monkeypatch.setattr("app.connectors.servicenow_connector.settings", settings_override)
    monkeypatch.setattr("app.connectors.base.settings", settings_override)

    connector = ServiceNowConnector(client=client)

    for _ in range(3):
        result = connector.fetch("INC0000001")
        assert result.error_code == "CONNECTION_ERROR"
    assert calls["n"] == 3

    # circuito agora aberto - a proxima chamada nao deve bater na rede
    blocked = connector.fetch("INC0000001")
    assert blocked.error_code == "CIRCUIT_OPEN"
    assert calls["n"] == 3  # handler NAO foi chamado de novo


def test_servicenow_connector_resets_circuit_on_success(monkeypatch):
    settings_override = Settings(
        servicenow_instance_url="https://demo.service-now.com",
        connector_circuit_failure_threshold=5,
    )
    monkeypatch.setattr("app.connectors.servicenow_connector.settings", settings_override)
    monkeypatch.setattr("app.connectors.base.settings", settings_override)

    failing_client, calls = _failing_client()
    connector = ServiceNowConnector(client=failing_client)
    for _ in range(4):
        connector.fetch("INC0000001")
    assert calls["n"] == 4
    assert connector_circuit_breaker.is_open("ServiceNow", cooldown_seconds=30.0) is False

    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": []})

    ok_client = httpx.Client(transport=httpx.MockTransport(ok_handler))
    ServiceNowConnector(client=ok_client).fetch("INC0000001")

    # sucesso reseta o contador - mais 4 falhas (menos que o threshold=5)
    # nao deveriam abrir o circuito.
    for _ in range(4):
        connector.fetch("INC0000001")
    assert connector_circuit_breaker.is_open("ServiceNow", cooldown_seconds=30.0) is False
