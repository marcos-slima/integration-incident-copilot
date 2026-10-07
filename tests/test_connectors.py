"""Testes unitarios dos conectores - SAP (mock) e HTTP real (mock +
httpx.MockTransport) - sem dependencias externas, rodam em qualquer
maquina, sem precisar de nenhuma stack no ar.

Avaliacao externa (medio prazo, item 7 - "Testes de contrato dos
conectores... contra respostas reais documentadas"): os testes
"*_real_mode_success" abaixo usam corpos de resposta carregados de
tests/cassettes/ (via tests/cassette_loader.py::load_cassette), no
formato documentado publicamente pela API real de cada sistema - nao
mais dicts inventados inline no teste. Ver tests/cassettes/README.md
para o que esta (e o que deliberadamente NAO esta) coberto."""

import httpx
import pytest

from app.config import Settings
from app.connectors import ConnectorResult, connector_status, get_connector
from app.connectors.ariba_connector import AribaConnector
from app.connectors.odata_connector import ODataConnector
from app.connectors.po_connector import POConnector
from app.connectors.rfc_connector import RFCConnector
from app.connectors.salesforce_connector import SalesforceConnector
from app.connectors.servicenow_connector import ServiceNowConnector
from app.connectors.workday_connector import WorkdayConnector
from app.exceptions import ConfigurationError
from tests.cassette_loader import load_cassette


def test_odata_connector_known_scenario(monkeypatch):
    monkeypatch.setattr("app.connectors.odata_connector.settings.odata_service_url", "")
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


def test_rfc_connector_use_real_without_pyrfc_raises_configuration_error(monkeypatch):
    # Simula o ambiente de CI onde pyrfc/SAP RFC SDK nao esta instalado.
    # Localmente o SDK pode estar disponivel (SDK 7.50 PL19 instalado em
    # /usr/local/sap/nwrfcsdk) - por isso forcamos HAS_PYRFC=False via
    # monkeypatch para garantir que o guardrail funciona independente do
    # ambiente local.
    monkeypatch.setattr("app.connectors.rfc_connector.HAS_PYRFC", False)
    with pytest.raises(ConfigurationError, match="pyrfc"):
        RFCConnector(use_real=True)


def test_rfc_connector_fetch_auto_enables_real_mode_when_sap_ashost_configured(monkeypatch):
    # get_connector("rfc") (o que /diagnose de fato usa) sempre instancia
    # RFCConnector() SEM use_real - antes do alinhamento com o criterio do
    # ODataConnector, isso significava que o conector real nunca era
    # alcancavel via /diagnose, mesmo com SAP_ASHOST configurado.
    monkeypatch.setattr("app.connectors.rfc_connector.settings.sap_ashost", "sapprd.example.com")
    monkeypatch.setattr("app.connectors.rfc_connector.HAS_PYRFC", True)
    calls: list[str] = []

    def _fake_fetch_real(self, identifier: str) -> ConnectorResult:
        calls.append(identifier)
        return ConnectorResult(
            source_system="RFC",
            status="ok",
            error_code=None,
            message="ok (fake)",
            raw="",
            is_mock=False,
        )

    monkeypatch.setattr(RFCConnector, "_fetch_real", _fake_fetch_real)

    connector = get_connector("rfc")
    assert connector.use_real is False  # get_connector nao passa use_real=True

    result = connector.fetch("0000000001234567")

    assert calls == ["0000000001234567"]
    assert result.is_mock is False


def test_rfc_connector_fetch_raises_configuration_error_when_sap_ashost_configured_without_pyrfc(
    monkeypatch,
):
    monkeypatch.setattr("app.connectors.rfc_connector.settings.sap_ashost", "sapprd.example.com")
    monkeypatch.setattr("app.connectors.rfc_connector.HAS_PYRFC", False)

    connector = get_connector("rfc")
    with pytest.raises(ConfigurationError, match="pyrfc"):
        connector.fetch("0000000001234567")


def test_rfc_connector_stays_mock_when_sap_ashost_not_configured(monkeypatch):
    monkeypatch.setattr("app.connectors.rfc_connector.settings.sap_ashost", "")
    connector = get_connector("rfc")
    result = connector.fetch("RFC-IDOC-51-DEMO")
    assert result.error_code == "51"


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
        return httpx.Response(200, json=load_cassette("servicenow_incident"))

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
        return httpx.Response(200, json=load_cassette("servicenow_incident_not_found"))

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
        return httpx.Response(200, json=load_cassette("cpi_message_status"))

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
        return httpx.Response(200, json=load_cassette("salesforce_case_query"))

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
        return httpx.Response(200, json=load_cassette("workday_integration_event"))

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
        return httpx.Response(200, json=load_cassette("ariba_purchase_order"))

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


def test_connector_status_reports_mock_for_all_when_unconfigured(monkeypatch):
    for attr in (
        "odata_service_url",
        "sap_ashost",
        "servicenow_instance_url",
        "salesforce_instance_url",
        "workday_tenant",
        "ariba_base_url",
        "sfsf_base_url",
        "cap_service_url",
        "apim_analytics_url",
        "po_base_url",
    ):
        monkeypatch.setattr(f"app.connectors.settings.{attr}", "")

    from app.connectors import connector_status

    status = connector_status()
    assert set(status) == {
        "odata",
        "rfc",
        "servicenow",
        "salesforce",
        "workday",
        "ariba",
        "successfactors",
        "cap",
        "apim",
        "po",
    }
    for info in status.values():
        assert info["status"] == "mock"


def test_connector_status_reports_real_when_setting_configured(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.settings.servicenow_instance_url", "https://dev.service-now.com"
    )

    from app.connectors import connector_status

    assert connector_status()["servicenow"]["status"] == "real"


def test_connector_status_reports_misconfigured_for_rfc_without_pyrfc(monkeypatch):
    monkeypatch.setattr("app.connectors.settings.sap_ashost", "sapprd.example.com")
    monkeypatch.setattr("app.connectors.HAS_PYRFC", False)

    from app.connectors import connector_status

    result = connector_status()["rfc"]
    assert result["status"] == "misconfigured"
    assert "pyrfc" in result["note"]


# ---------------------------------------------------------------------------
# SuccessFactors connector (DA-34)
# ---------------------------------------------------------------------------


def test_successfactors_connector_demo_mode_known_scenario(monkeypatch):
    monkeypatch.setattr("app.connectors.successfactors_connector.settings.sfsf_base_url", "")
    connector = get_connector("successfactors")

    result = connector.fetch("SFSF-REPL-FAIL-DEMO")

    assert result.status == "error"
    assert result.error_code == "REPLICATION_FAILED"
    assert result.source_system == "SuccessFactors"
    assert "MDI" in result.message or "replicacao" in result.message.lower()


def test_successfactors_connector_demo_mode_inactive_employee(monkeypatch):
    monkeypatch.setattr("app.connectors.successfactors_connector.settings.sfsf_base_url", "")
    connector = get_connector("successfactors")

    result = connector.fetch("SFSF-INACTIVE-DEMO")

    assert result.status == "error"
    assert result.error_code == "EMPLOYEE_INACTIVE"


def test_successfactors_connector_demo_mode_unknown_returns_default(monkeypatch):
    monkeypatch.setattr("app.connectors.successfactors_connector.settings.sfsf_base_url", "")
    connector = get_connector("successfactors")

    result = connector.fetch("ID-NAO-EXISTE")

    assert result.is_fallback is True
    assert result.source_system == "SuccessFactors"


def test_successfactors_connector_real_mode_active_employee(monkeypatch):
    from app.config import Settings
    from app.connectors.successfactors_connector import SuccessFactorsConnector

    monkeypatch.setattr(
        "app.connectors.successfactors_connector.settings",
        Settings(
            sfsf_base_url="https://api4.successfactors.com",
            sfsf_oauth_token_url="https://acme.auth.us10.hana.ondemand.com/oauth/token",
            sfsf_client_id="cid",
            sfsf_client_secret="csecret",
        ),
    )

    cassette = load_cassette("successfactors_employee")

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth" in str(request.url) or "token" in str(request.url):
            return httpx.Response(200, json={"access_token": "fake-token"})
        return httpx.Response(200, json=cassette)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = SuccessFactorsConnector(client=client).fetch("SFSF-REPL-OK-TEST")

    assert result.status == "ok"
    assert result.is_mock is False
    assert result.source_system == "SuccessFactors"


def _sf_settings(monkeypatch):
    from app.config import Settings

    monkeypatch.setattr(
        "app.connectors.successfactors_connector.settings",
        Settings(
            sfsf_base_url="https://api4.successfactors.com",
            sfsf_oauth_token_url="https://acme.auth.us10.hana.ondemand.com/oauth/token",
            sfsf_client_id="cid",
            sfsf_client_secret="csecret",
        ),
    )


def test_successfactors_connector_real_mode_terminated(monkeypatch):
    """M-07: leitura pela CHAVE PerPerson('<id>') e status pelo endDate do
    EmpEmployment (replicationStatus/employmentStatus nao existem na API)."""
    from app.connectors.successfactors_connector import SuccessFactorsConnector

    _sf_settings(monkeypatch)
    urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth" in str(request.url) or "token" in str(request.url):
            return httpx.Response(200, json={"access_token": "fake-token"})
        urls.append(request.url)
        return httpx.Response(
            200,
            json={
                "d": {
                    "personIdExternal": "EMP-999",
                    "employmentNav": {"results": [{"endDate": "/Date(1767225600000)/"}]},
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = SuccessFactorsConnector(client=client).fetch("EMP-999")

    assert urls[0].path == "/odata/v2/PerPerson('EMP-999')"
    assert "personIdExternal" not in urls[0].params
    assert result.status == "error"
    assert result.error_code == "TERMINATED"
    assert result.is_mock is False


def test_m07_successfactors_nao_usa_registro_de_outro_funcionario(monkeypatch):
    from app.connectors.successfactors_connector import SuccessFactorsConnector

    _sf_settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if "token" in str(request.url):
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(200, json={"d": {"personIdExternal": "OUTRO", "employmentNav": {}}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = SuccessFactorsConnector(client=client).fetch("EMP-1")
    assert result.error_code == "WRONG_RECORD"

    def lista(request: httpx.Request) -> httpx.Response:
        if "token" in str(request.url):
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(200, json={"d": {"results": [{"personIdExternal": "OUTRO"}]}})

    client = httpx.Client(transport=httpx.MockTransport(lista))
    assert SuccessFactorsConnector(client=client).fetch("EMP-1").error_code == "INVALID_RESPONSE"


def test_successfactors_connector_real_mode_connection_error(monkeypatch):
    from app.config import Settings
    from app.connectors.successfactors_connector import SuccessFactorsConnector

    monkeypatch.setattr(
        "app.connectors.successfactors_connector.settings",
        Settings(
            sfsf_base_url="https://api4.successfactors.com",
            sfsf_oauth_token_url="https://acme.auth.us10.hana.ondemand.com/oauth/token",
            sfsf_client_id="cid",
            sfsf_client_secret="csecret",
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth" in str(request.url) or "token" in str(request.url):
            return httpx.Response(200, json={"access_token": "fake-token"})
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = SuccessFactorsConnector(client=client).fetch("EMP-ERR")

    assert result.error_code == "CONNECTION_ERROR"
    assert result.is_fallback is True


def test_successfactors_connector_real_mode_401(monkeypatch):
    from app.config import Settings
    from app.connectors.successfactors_connector import SuccessFactorsConnector

    monkeypatch.setattr(
        "app.connectors.successfactors_connector.settings",
        Settings(
            sfsf_base_url="https://api4.successfactors.com",
            sfsf_oauth_token_url="https://acme.auth.us10.hana.ondemand.com/oauth/token",
            sfsf_client_id="cid",
            sfsf_client_secret="csecret",
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth" in str(request.url) or "token" in str(request.url):
            return httpx.Response(200, json={"access_token": "fake-token"})
        return httpx.Response(401, text="Unauthorized")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = SuccessFactorsConnector(client=client).fetch("EMP-401")

    assert result.error_code == "401"
    assert result.is_fallback is True


# ---------------------------------------------------------------------------
# DA-56: SAP PO/PI (on-premise)
# ---------------------------------------------------------------------------


def test_po_connector_registrado_e_status_mock_sem_config(monkeypatch):
    """Registry + `connector_status` (a mesma fonte que GET /health usa)."""
    monkeypatch.setattr("app.connectors.po_connector.settings.po_base_url", "")
    connector = get_connector("po")
    assert isinstance(connector, POConnector)
    assert connector_status()["po"]["status"] == "mock"


def test_po_connector_mocks_conhecidos():
    result = POConnector().fetch("PO-FAILED-001")
    assert result.status == "error"  # FAILED e' o incidente
    assert result.error_code == "FAILED"
    assert result.is_mock is True
    assert "Z_S4_ORDER_OUT" in result.message


def test_po_connector_modo_lista_por_filtro():
    """Sem `po_base_url`, os filtros de status devolvem a lista mock em vez
    do fallback 'nao encontrado' — o Message Monitor e' justamente um
    monitor de mensagens em falha."""
    for filtro in ("FAILED", "HOLDING", "ALL", ""):
        result = POConnector().fetch(filtro)
        assert result.is_fallback is False
        assert "PO/PI" in result.message


def test_po_connector_identificador_desconhecido_cai_em_fallback():
    result = POConnector().fetch("PO-NAO-EXISTE")
    assert result.is_mock is True
    assert result.is_fallback is True


def test_po_connector_use_real_sem_url_falha_alto():
    """Padrao do ODataConnector: pedir real sem configuracao e' erro
    explicito, nao queda silenciosa em mock."""
    with pytest.raises(ConfigurationError):
        POConnector(use_real=True)


def test_po_connector_oauth2_sem_token_url_falha_alto(monkeypatch):
    """Fail-closed: modo oauth2 sem token URL nao pode cair em Basic Auth
    (mandaria Basic para um token endpoint e receberia 401 sem explicacao)."""
    monkeypatch.setattr("app.connectors.po_connector.settings.po_auth_mode", "oauth2")
    monkeypatch.setattr("app.connectors.po_connector.settings.po_oauth_token_url", "")
    monkeypatch.setattr(
        "app.connectors.po_connector.settings.po_base_url", "https://po.example.com"
    )
    with pytest.raises(ConfigurationError):
        POConnector()


def test_m08_po_oauth2_sem_token_url_em_mock_nao_derruba(monkeypatch):
    """Validacao 2026-10-07 (M-08): sem PO_BASE_URL o conector e demo e nunca
    fala OAuth2 - a checagem derrubava TODO incidente `po` com 500."""
    monkeypatch.setattr("app.connectors.po_connector.settings.po_auth_mode", "oauth2")
    monkeypatch.setattr("app.connectors.po_connector.settings.po_oauth_token_url", "")
    monkeypatch.setattr("app.connectors.po_connector.settings.po_base_url", "")
    assert POConnector().fetch("PO-MSG-DEMO").is_mock is True


def test_po_connector_real_mode_basic_auth(monkeypatch):
    """Caminho HTTP REAL via MockTransport, com Basic Auth nativo (o PO/PI
    nao tem OAuth2)."""
    monkeypatch.setattr(
        "app.connectors.po_connector.settings",
        Settings(po_base_url="https://wd-dmz.corp.example/po", po_username="u", po_password="p"),
    )
    vistos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        assert request.url.path.endswith("/mdt/api/1.0/facade")
        assert request.headers["Authorization"].startswith("Basic ")
        return httpx.Response(200, json=load_cassette("po_message_monitor"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = POConnector(client=client).fetch("FAILED")

    assert len(vistos) == 1
    assert vistos[0].url.params["type"] == "message"
    assert vistos[0].url.params["status"] == "FAILED"
    assert result.status == "error"
    assert result.error_code == "FAILED"
    assert result.is_mock is False
    assert result.is_fallback is False
    assert "Connection refused" in result.message


def test_po_connector_real_mode_detalhe_por_message_id(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.po_connector.settings",
        Settings(po_base_url="https://wd-dmz.corp.example/po", po_username="u", po_password="p"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["id"] == "PO-FAILED-001"
        assert "status" not in request.url.params
        return httpx.Response(200, json=load_cassette("po_message_monitor"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = POConnector(client=client).fetch("PO-FAILED-001")
    assert result.is_mock is False
    assert "PO-FAILED-001" in result.message


def test_po_connector_oauth2_quando_apim_esta_na_frente(monkeypatch):
    """Modo oauth2: token endpoint do APIM + Bearer na chamada do Message
    Monitor. E' o mesmo contrato de `odata_connector`/`ariba_connector`."""
    monkeypatch.setattr(
        "app.connectors.po_connector.settings",
        Settings(
            po_base_url="https://apim.corp.example/po",
            po_auth_mode="oauth2",
            po_oauth_token_url="https://apim.corp.example/oauth/token",
            po_oauth_client_id="cid",
            po_oauth_client_secret="csecret",
        ),
    )
    auths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auths.append(request.headers.get("Authorization", ""))
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "tok-123"})
        return httpx.Response(200, json=load_cassette("po_message_monitor"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = POConnector(client=client).fetch("FAILED")

    assert auths[0].startswith("Basic ")  # token endpoint = Client Credentials
    assert auths[1] == "Bearer tok-123"
    assert result.is_mock is False


def test_po_connector_parser_tolera_envelopes_e_formatos():
    """A API do Message Monitor nao e' publica nem estavel: o parser tem de
    tolerar lista direta, envelope `messages`, envelope OData (`d.results`)
    e objeto unico — um formato inesperado vira evidencia, nao traceback."""
    from app.connectors.po_connector import _extract_messages

    msg = {"messageId": "M1", "status": "FAILED"}
    assert _extract_messages([msg]) == [msg]
    assert _extract_messages({"messages": [msg]}) == [msg]
    assert _extract_messages({"messageLog": [msg]}) == [msg]
    assert _extract_messages({"d": {"results": [msg]}}) == [msg]
    assert _extract_messages(msg) == [msg]
    assert _extract_messages("lixo") == []
    assert _extract_messages(None) == []


def test_po_connector_resposta_nao_json_vira_evidencia(monkeypatch):
    """O Message Monitor pode responder XML conforme patch/release. Um
    JSONDecodeError aqui viraria traceback no grafo; tem de virar erro
    legivel com o caminho verificado."""
    monkeypatch.setattr(
        "app.connectors.po_connector.settings",
        Settings(po_base_url="https://wd-dmz.corp.example/po", po_username="u", po_password="p"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<messageLog><entry/></messageLog>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = POConnector(client=client).fetch("FAILED")
    assert result.error_code == "UNEXPECTED_CONTENT_TYPE"
    assert result.is_mock is False
    assert "/mdt/api/1.0/facade" in result.message


def test_po_connector_rejeita_identifier_com_injection(monkeypatch):
    """O identifier vem de entrada do usuario e vai para a query string.
    `validate_identifier_charset` e' o que impede injecao nos conectores
    reais (ver app/connectors/base.py) — o PO/PI nao pode ser a excecao."""
    monkeypatch.setattr(
        "app.connectors.po_connector.settings",
        Settings(po_base_url="https://wd-dmz.corp.example/po", po_username="u", po_password="p"),
    )
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("nao deveria chamar a rede"))
    )
    result = POConnector(client=client).fetch("x' or 1 eq 1")
    assert result.error_code == "INVALID_IDENTIFIER"
    assert result.is_mock is False


def test_po_connector_http_error_e_fallback(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.po_connector.settings",
        Settings(po_base_url="https://wd-dmz.corp.example/po", po_username="u", po_password="p"),
    )
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(403, text="Forbidden"))
    )
    result = POConnector(client=client).fetch("FAILED")
    assert result.error_code == "403"
    assert result.is_fallback is True


def test_po_connector_token_401_vira_result_e_nao_excecao(monkeypatch):
    """REGRESSAO CORRIGIDA. O `raise_for_status` do token endpoint estoura
    `httpx.HTTPStatusError`, que NAO e subclasse de `RequestError`: um 401 do
    APIM subia como excecao em vez de virar `ConnectorResult`. O grafo veria
    500 em vez de um conector degradado com evidencia — e o circuit breaker
    nem registrava a falha, que e' justamente o sinal que ele existe para
    capturar. Mesmo par de `except` do `ODataConnector` e `AribaConnector`.
    """
    monkeypatch.setattr(
        "app.connectors.po_connector.settings",
        Settings(
            po_base_url="https://apim.corp.example/po",
            po_auth_mode="oauth2",
            po_oauth_token_url="https://apim.corp.example/oauth/token",
            po_oauth_client_id="id",
            po_oauth_client_secret="seg",
        ),
    )
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(401, text="invalid_client"))
    )
    result = POConnector(client=client).fetch("FAILED")
    assert result.status == "error"
    assert result.error_code == "401"
    assert result.is_mock is False
    assert result.is_fallback is True
    assert "401" in result.message


def test_po_connector_token_sem_access_token_vira_result(monkeypatch):
    """200 sem `access_token`, ou corpo que nao e JSON: um APIM mal
    configurado faz isso sem nenhum erro HTTP. Sem este caminho, a KeyError
    do dict ou o JSONDecodeError subiam como excecao."""
    monkeypatch.setattr(
        "app.connectors.po_connector.settings",
        Settings(
            po_base_url="https://apim.corp.example/po",
            po_auth_mode="oauth2",
            po_oauth_token_url="https://apim.corp.example/oauth/token",
            po_oauth_client_id="id",
            po_oauth_client_secret="seg",
        ),
    )
    # B023: a lambda precisa amarrar `corpo` no default. Sem isso ela fecha
    # sobre a variavel do loop e as duas iteracoes testariam o mesmo corpo —
    # o dict sem `access_token` passaria sem nunca ter sido exercitado.
    for corpo in ({"token_type": "Bearer"}, "<html>proxy error</html>"):
        client = httpx.Client(
            transport=httpx.MockTransport(lambda r, c=corpo: httpx.Response(200, json=c))
        )
        result = POConnector(client=client).fetch("FAILED")
        assert result.status == "error"
        assert result.is_fallback is True


def test_po_connector_erro_de_rede_registra_no_circuit_breaker(monkeypatch):
    """O caminho de rede tambem estava sem teste: e' o unico motivo de o
    breaker existir no conector, e uma regressao ali passaria despercebida."""
    monkeypatch.setattr(
        "app.connectors.po_connector.settings",
        Settings(po_base_url="https://wd-dmz.corp.example/po", po_username="u", po_password="p"),
    )

    def estoura(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=r)

    client = httpx.Client(transport=httpx.MockTransport(estoura))
    result = POConnector(client=client).fetch("FAILED")
    assert result.status == "error"
    assert result.error_code == "CONNECTION_ERROR"
    assert result.is_fallback is True


def test_po_connector_summary_limita_linhas(monkeypatch):
    """Teto de linhas no resumo: o `raw` completo vai junto para o RAG, mas
    o texto injetado no prompt do LLM nao pode crescer sem limite."""
    from app.connectors.po_connector import _MAX_SUMMARIZED, _summarize

    mensagens = [{"messageId": f"M{i}", "status": "FAILED"} for i in range(25)]
    resumo = _summarize(mensagens)
    assert len(resumo.splitlines()) == _MAX_SUMMARIZED + 1
    assert "+15 mensagens" in resumo
