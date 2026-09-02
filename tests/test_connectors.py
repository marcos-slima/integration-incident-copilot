"""Testes unitarios dos conectores - SAP (mock) e ServiceNow (mock +
HTTP real via MockTransport) - sem dependencias externas, rodam em
qualquer maquina, sem precisar de nenhuma stack no ar.
"""

import httpx
import pytest

from app.config import Settings
from app.connectors import get_connector
from app.connectors.odata_connector import ODataConnector
from app.connectors.rfc_connector import RFCConnector
from app.connectors.servicenow_connector import ServiceNowConnector
from app.exceptions import ConfigurationError


def test_odata_connector_known_scenario():
    connector = get_connector("odata")
    assert isinstance(connector, ODataConnector)

    result = connector.fetch("CPI-401-DEMO")
    assert result.status == "error"
    assert result.error_code == "401"
    assert "Unauthorized" in result.message
    assert result.is_mock is True


def test_odata_connector_unknown_identifier_returns_safe_fallback():
    connector = get_connector("odata")
    result = connector.fetch("ID-QUE-NAO-EXISTE")

    # nao deve inventar um cenario especifico - deve cair no fallback
    # generico e continuar marcado como mock
    assert result.is_mock is True
    assert result.error_code == "500"


def test_rfc_connector_known_scenarios():
    connector = get_connector("rfc")
    assert isinstance(connector, RFCConnector)

    conn_refused = connector.fetch("RFC-CONN-REFUSED-DEMO")
    assert conn_refused.error_code == "RFC_COMMUNICATION_FAILURE"

    idoc_51 = connector.fetch("RFC-IDOC-51-DEMO")
    assert idoc_51.error_code == "51"
    assert "material" in idoc_51.raw.lower()


def test_get_connector_invalid_type_raises():
    with pytest.raises(ValueError):
        get_connector("soap")  # tipo nao suportado


def test_rfc_connector_gateway_pool_timeout_scenario():
    connector = get_connector("rfc")
    result = connector.fetch("RFC-GWY-POOL-TIMEOUT-DEMO")
    assert result.error_code == "RFC_GWY_POOL_EXHAUSTED"
    assert "pool" in result.message.lower()


def test_rfc_connector_use_real_without_pyrfc_raises_configuration_error():
    # No ambiente de CI/dev nao ha pyrfc/SAP RFC SDK instalado - o
    # objetivo deste teste e garantir que isso falha alto e claro,
    # nunca cai silenciosamente no mock quando use_real=True foi pedido
    # explicitamente.
    with pytest.raises(ConfigurationError, match="pyrfc"):
        RFCConnector(use_real=True)


def test_servicenow_connector_demo_mode_known_scenario():
    connector = get_connector("servicenow")
    assert isinstance(connector, ServiceNowConnector)

    result = connector.fetch("INC0010001")
    assert result.source_system == "ServiceNow"
    assert result.is_mock is True
    assert "RFC" in result.message


def test_servicenow_connector_demo_mode_unknown_identifier_returns_fallback():
    result = ServiceNowConnector().fetch("INC-NAO-EXISTE")
    assert result.is_mock is True
    assert result.is_fallback is True


def test_servicenow_connector_real_mode_success(monkeypatch):
    """Exercita o caminho HTTP REAL (nao o mock) via MockTransport -
    prova que o conector monta a chamada certa e interpreta a resposta
    certa, sem depender de uma instancia ServiceNow de verdade."""
    monkeypatch.setattr(
        "app.connectors.servicenow_connector.settings",
        Settings(
            servicenow_instance_url="https://demo.service-now.com",
            servicenow_username="user",
            servicenow_password="pass",
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["sysparm_query"] == "number=INC0099999"
        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "number": "INC0099999",
                        "priority": "2 - High",
                        "short_description": "SAP OData endpoint timing out",
                        "category": "Integration",
                        "cmdb_ci": "SAP S/4HANA PRD",
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = ServiceNowConnector(client=client).fetch("INC0099999")

    assert result.status == "ok"
    assert result.is_mock is False
    assert result.error_code == "2 - High"
    assert "OData" in result.message


def test_servicenow_connector_real_mode_not_found(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.servicenow_connector.settings",
        Settings(servicenow_instance_url="https://demo.service-now.com"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = ServiceNowConnector(client=client).fetch("INC-INEXISTENTE")

    assert result.status == "error"
    assert result.error_code == "404"
    assert result.is_mock is False
    assert result.is_fallback is True


def test_servicenow_connector_real_mode_connection_error(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.servicenow_connector.settings",
        Settings(servicenow_instance_url="https://demo.service-now.com"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = ServiceNowConnector(client=client).fetch("INC0000001")

    assert result.error_code == "CONNECTION_ERROR"
    assert result.is_fallback is True
