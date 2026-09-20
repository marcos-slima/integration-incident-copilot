"""Testes de app/connectors/base.py::validate_identifier_charset -
avaliacao externa (nova revisao, P1 - "Injection nos conectores
reais"). Cada conector real (OData, Salesforce, ServiceNow, CAP,
Workday, Ariba) agora rejeita um identifier fora do charset permitido
ANTES de montar a query/URL ou abrir qualquer conexao HTTP - os testes
de injection abaixo usam um `httpx.MockTransport` cujo handler levanta
AssertionError se for chamado, provando que a rede nunca e alcancada
para um identifier invalido."""

import httpx
import pytest

from app.config import Settings
from app.connectors.ariba_connector import AribaConnector
from app.connectors.base import validate_identifier_charset
from app.connectors.cap_connector import CAPConnector
from app.connectors.odata_connector import ODataConnector
from app.connectors.salesforce_connector import SalesforceConnector
from app.connectors.servicenow_connector import ServiceNowConnector
from app.connectors.workday_connector import WorkdayConnector


def _network_should_not_be_called(request: httpx.Request) -> httpx.Response:
    raise AssertionError(
        f"rede alcancada para um identifier invalido - o guard de charset deveria "
        f"ter bloqueado antes: {request.url}"
    )


@pytest.mark.parametrize(
    "identifier",
    [
        "x' or 1 eq 1",
        "x' OR '1'='1",
        "../outro-recurso",
        "id?foo=bar",
        "id#fragment",
        "id with spaces",
        "",
        "a" * 201,
    ],
)
def test_validate_identifier_charset_rejects_unsafe_identifiers(identifier):
    result = validate_identifier_charset(identifier, "TesteSystem")
    assert result is not None
    assert result.error_code == "INVALID_IDENTIFIER"
    assert result.status == "error"
    assert result.is_fallback is True


@pytest.mark.parametrize(
    "identifier",
    ["CPI-401-DEMO", "INC0099999", "PO-42", "EVT-123", "CaseNumber.00847", "abc_123-XYZ"],
)
def test_validate_identifier_charset_accepts_legit_identifiers(identifier):
    assert validate_identifier_charset(identifier, "TesteSystem") is None


def test_odata_connector_real_mode_rejects_injection_identifier_without_network(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.odata_connector.settings",
        Settings(
            odata_service_url="https://tenant.cpi.example.com/MessageStatus",
            odata_oauth_token_url="https://tenant.authentication.example.com/oauth/token",
            odata_client_id="client-id",
            odata_client_secret="client-secret",
        ),
    )
    client = httpx.Client(transport=httpx.MockTransport(_network_should_not_be_called))
    result = ODataConnector(client=client).fetch("x' or 1 eq 1")

    assert result.error_code == "INVALID_IDENTIFIER"
    assert result.is_fallback is True


def test_salesforce_connector_real_mode_rejects_injection_identifier_without_network(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.salesforce_connector.settings",
        Settings(
            salesforce_instance_url="https://demo.my.salesforce.com",
            salesforce_client_id="cid",
            salesforce_client_secret="csecret",
        ),
    )
    client = httpx.Client(transport=httpx.MockTransport(_network_should_not_be_called))
    result = SalesforceConnector(client=client).fetch("x' OR '1'='1")

    assert result.error_code == "INVALID_IDENTIFIER"
    assert result.is_fallback is True


def test_servicenow_connector_real_mode_rejects_injection_identifier_without_network(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.servicenow_connector.settings",
        Settings(servicenow_instance_url="https://demo.service-now.com"),
    )
    client = httpx.Client(transport=httpx.MockTransport(_network_should_not_be_called))
    result = ServiceNowConnector(client=client).fetch("INC0000001^ORnumber=INC0000002")

    assert result.error_code == "INVALID_IDENTIFIER"
    assert result.is_fallback is True


def test_cap_connector_real_mode_rejects_injection_identifier_without_network(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.cap_connector.settings.cap_service_url", "https://fake.cap/odata/v4/svc"
    )
    monkeypatch.setattr(
        "app.connectors.cap_connector.settings.cap_xsuaa_token_url",
        "https://fake.xsuaa/oauth/token",
    )
    monkeypatch.setattr("app.connectors.cap_connector.settings.cap_client_id", "fake-client")
    monkeypatch.setattr("app.connectors.cap_connector.settings.cap_client_secret", "fake-secret")

    client = httpx.Client(transport=httpx.MockTransport(_network_should_not_be_called))
    result = CAPConnector(client=client).fetch("x' or 1 eq 1")

    assert result.error_code == "INVALID_IDENTIFIER"
    assert result.is_fallback is True


def test_workday_connector_real_mode_rejects_path_injection_identifier_without_network(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.connectors.workday_connector.settings",
        Settings(
            workday_tenant="acme",
            workday_rest_base_url="https://wd2-impl.workday.com/ccx/api/v1/acme",
            workday_client_id="cid",
            workday_client_secret="csecret",
        ),
    )
    client = httpx.Client(transport=httpx.MockTransport(_network_should_not_be_called))
    result = WorkdayConnector(client=client).fetch("../../admin/secrets")

    assert result.error_code == "INVALID_IDENTIFIER"
    assert result.is_fallback is True


def test_ariba_connector_real_mode_rejects_path_injection_identifier_without_network(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.ariba_connector.settings",
        Settings(
            ariba_base_url="https://openapi.ariba.com/api/purchase-orders",
            ariba_oauth_token_url="https://api.ariba.com/v2/oauth/token",
            ariba_client_id="cid",
            ariba_client_secret="csecret",
        ),
    )
    client = httpx.Client(transport=httpx.MockTransport(_network_should_not_be_called))
    result = AribaConnector(client=client).fetch("../../admin/secrets")

    assert result.error_code == "INVALID_IDENTIFIER"
    assert result.is_fallback is True
