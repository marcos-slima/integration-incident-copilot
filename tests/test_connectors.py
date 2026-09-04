"""Testes unitarios dos conectores - SAP (mock) e ServiceNow (mock +
HTTP real via MockTransport) - sem dependencias externas, rodam em
qualquer maquina, sem precisar de nenhuma stack no ar.
"""

import httpx
import pytest

from app.config import Settings
from app.connectors import get_connector
from app.connectors.ariba_connector import AribaConnector
from app.connectors.odata_connector import ODataConnector
from app.connectors.rfc_connector import RFCConnector
from app.connectors.salesforce_connector import SalesforceConnector
from app.connectors.servicenow_connector import ServiceNowConnector
from app.connectors.workday_connector import WorkdayConnector
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


def test_servicenow_connector_demo_mode_known_scenario(monkeypatch):
    monkeypatch.setattr("app.connectors.servicenow_connector.settings.servicenow_instance_url", "")
    connector = get_connector("servicenow")
    assert isinstance(connector, ServiceNowConnector)

    result = connector.fetch("INC0010001")
    assert result.source_system == "ServiceNow"
    assert result.is_mock is True
    assert "RFC" in result.message


def test_servicenow_connector_demo_mode_unknown_identifier_returns_fallback(monkeypatch):
    monkeypatch.setattr("app.connectors.servicenow_connector.settings.servicenow_instance_url", "")
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


def test_odata_connector_real_mode_success(monkeypatch):
    """Exercita o caminho HTTP REAL do OData (token OAuth2 + GET no
    servico) via MockTransport - mesmo criterio de rigor usado para o
    ServiceNow: nao ha SAP Integration Suite real disponivel, mas o
    codigo de producao (fetch de token, header Bearer, parsing OData
    v2) e exercitado de verdade."""
    monkeypatch.setattr(
        "app.connectors.odata_connector.settings",
        Settings(
            odata_service_url="https://tenant.cpi.example.com/MessageStatus",
            odata_oauth_token_url="https://tenant.authentication.example.com/oauth/token",
            odata_client_id="client-id",
            odata_client_secret="client-secret",
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "fake-token"})
        assert request.headers["Authorization"] == "Bearer fake-token"
        assert "MSG-001-DEMO" in request.url.params["$filter"]
        return httpx.Response(
            200,
            json={
                "d": {
                    "results": [
                        {
                            "MessageId": "MSG-001-DEMO",
                            "Status": "FAILED",
                            "StatusText": "Timeout no adapter HTTP de destino",
                        }
                    ]
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = ODataConnector(client=client).fetch("MSG-001-DEMO")

    assert result.status == "error"
    assert result.is_mock is False
    assert result.error_code == "FAILED"
    assert "Timeout" in result.message


def test_odata_connector_use_real_without_service_url_raises_configuration_error():
    # Mesmo criterio do RFCConnector: use_real=True pedido
    # explicitamente sem configuracao suficiente falha alto e claro,
    # nunca cai silenciosamente em mock.
    with pytest.raises(ConfigurationError, match="ODATA_SERVICE_URL"):
        ODataConnector(use_real=True)


def test_salesforce_connector_demo_mode_known_scenario(monkeypatch):
    monkeypatch.setattr("app.connectors.salesforce_connector.settings.salesforce_instance_url", "")
    connector = get_connector("salesforce")
    assert isinstance(connector, SalesforceConnector)

    result = connector.fetch("SF-CASE-00847-DEMO")
    assert result.source_system == "Salesforce"
    assert result.is_mock is True
    assert "SAP" in result.message


def test_salesforce_connector_real_mode_success(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.salesforce_connector.settings",
        Settings(
            salesforce_instance_url="https://demo.my.salesforce.com",
            salesforce_client_id="cid",
            salesforce_client_secret="csecret",
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "fake-token"})
        assert "00847" in request.url.params["q"]
        return httpx.Response(
            200,
            json={
                "records": [
                    {
                        "CaseNumber": "00847",
                        "Priority": "High",
                        "Subject": "Pedido criado no Salesforce nao aparece no SAP SD",
                        "Status": "Working",
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = SalesforceConnector(client=client).fetch("00847")

    assert result.status == "ok"
    assert result.is_mock is False
    assert result.error_code == "High"


def test_salesforce_connector_real_mode_not_found(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.salesforce_connector.settings",
        Settings(salesforce_instance_url="https://demo.my.salesforce.com"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "fake-token"})
        return httpx.Response(200, json={"records": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = SalesforceConnector(client=client).fetch("00000")

    assert result.error_code == "404"
    assert result.is_fallback is True


def test_workday_connector_demo_mode_known_scenario(monkeypatch):
    monkeypatch.setattr("app.connectors.workday_connector.settings.workday_tenant", "")
    connector = get_connector("workday")
    assert isinstance(connector, WorkdayConnector)

    result = connector.fetch("WD-SYNC-FAIL-DEMO")
    assert result.source_system == "Workday"
    assert result.is_mock is True
    assert "SuccessFactors" in result.message


def test_workday_connector_real_mode_success(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.workday_connector.settings",
        Settings(
            workday_tenant="acme",
            workday_rest_base_url="https://wd2-impl.workday.com/ccx/api/v1/acme",
            workday_client_id="cid",
            workday_client_secret="csecret",
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2" in str(request.url):
            return httpx.Response(200, json={"access_token": "fake-token"})
        assert request.url.path.endswith("/integrationEvents/EVT-123")
        return httpx.Response(
            200,
            json={"status": "Error", "errorMessage": "Worker_ID nao encontrado"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = WorkdayConnector(client=client).fetch("EVT-123")

    assert result.status == "error"
    assert result.is_mock is False
    assert "Worker_ID" in result.message


def test_workday_connector_real_mode_not_found(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.workday_connector.settings",
        Settings(
            workday_tenant="acme",
            workday_rest_base_url="https://wd2-impl.workday.com/ccx/api/v1/acme",
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2" in str(request.url):
            return httpx.Response(200, json={"access_token": "fake-token"})
        return httpx.Response(404, text="not found")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = WorkdayConnector(client=client).fetch("EVT-NAO-EXISTE")

    assert result.error_code == "404"
    assert result.is_fallback is True


def test_ariba_connector_demo_mode_known_scenario(monkeypatch):
    monkeypatch.setattr("app.connectors.ariba_connector.settings.ariba_base_url", "")
    connector = get_connector("ariba")
    assert isinstance(connector, AribaConnector)

    result = connector.fetch("ARIBA-PO-BLOCKED-DEMO")
    assert result.source_system == "Ariba"
    assert result.is_mock is True
    assert result.error_code == "SUPPLIER_MISMATCH"


def test_ariba_connector_real_mode_success(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.ariba_connector.settings",
        Settings(
            ariba_base_url="https://openapi.ariba.com/api/purchase-orders",
            ariba_oauth_token_url="https://api.ariba.com/v2/oauth/token",
            ariba_client_id="cid",
            ariba_client_secret="csecret",
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth" in str(request.url):
            return httpx.Response(200, json={"access_token": "fake-token"})
        assert request.url.path.endswith("/purchase-orders/PO-42")
        return httpx.Response(
            200,
            json={
                "networkStatus": "Failed",
                "errorCode": "SUPPLIER_MISMATCH",
                "detail": "ANID divergente do cadastro em S/4HANA",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = AribaConnector(client=client).fetch("PO-42")

    assert result.status == "error"
    assert result.is_mock is False
    assert result.error_code == "SUPPLIER_MISMATCH"


def test_ariba_connector_real_mode_connection_error(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.ariba_connector.settings",
        Settings(
            ariba_base_url="https://openapi.ariba.com/api/purchase-orders",
            ariba_oauth_token_url="https://api.ariba.com/v2/oauth/token",
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth" in str(request.url):
            return httpx.Response(200, json={"access_token": "fake-token"})
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = AribaConnector(client=client).fetch("PO-99")

    assert result.error_code == "CONNECTION_ERROR"
    assert result.is_fallback is True
