"""Validacao 2026-10-07, Bloco 3: conectores (M-06 a M-10)."""

from __future__ import annotations

import sys
import types

import httpx
import pytest

from app.config import Settings
from app.connectors.base import connector_circuit_breaker


def _odata(monkeypatch, handler, **extra):
    from app.connectors.odata_connector import ODataConnector

    monkeypatch.setattr(
        "app.connectors.odata_connector.settings",
        Settings(
            odata_service_url="https://cpi.example.com/api/v1/MessageProcessingLogs",
            odata_oauth_token_url="https://cpi.example.com/oauth/token",
            odata_client_id="cid",
            odata_client_secret="sec",
            **extra,
        ),
    )
    monkeypatch.setattr(
        "app.connectors.base.settings",
        Settings(connector_circuit_failure_threshold=3, connector_circuit_cooldown_seconds=60),
    )
    return ODataConnector(client=httpx.Client(transport=httpx.MockTransport(handler)))


def _token_ou(resposta):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return resposta(request)

    return handler


# ---------------------------------------------------------------------------
# M-06: breaker classifica erros, JSON seguro, cache de token
# ---------------------------------------------------------------------------


def test_m06_5xx_abre_o_circuito(monkeypatch):
    conn = _odata(monkeypatch, _token_ou(lambda r: httpx.Response(503, text="down")))
    for _ in range(3):
        assert conn.fetch("MSG1").error_code == "503"
    assert conn.fetch("MSG1").error_code == "CIRCUIT_OPEN"


def test_m06_4xx_nao_abre_o_circuito(monkeypatch):
    conn = _odata(monkeypatch, _token_ou(lambda r: httpx.Response(403, text="forbidden")))
    for _ in range(5):
        assert conn.fetch("MSG1").error_code == "403"


def test_m06_token_401_nao_abre_o_circuito(monkeypatch):
    def handler(request):
        return httpx.Response(401, text="invalid_client")

    conn = _odata(monkeypatch, handler)
    for _ in range(5):
        assert conn.fetch("MSG1").error_code == "401"
    assert not connector_circuit_breaker.is_open("OData", 60)


def test_m06_200_nao_json_vira_resultado_e_nao_500(monkeypatch):
    conn = _odata(monkeypatch, _token_ou(lambda r: httpx.Response(200, text="<html>login</html>")))
    result = conn.fetch("MSG1")
    assert result.error_code == "INVALID_RESPONSE" and result.is_fallback


def test_m06_token_sem_access_token(monkeypatch):
    conn = _odata(monkeypatch, lambda r: httpx.Response(200, json={"erro": "x"}))
    assert conn.fetch("MSG1").error_code == "INVALID_TOKEN_RESPONSE"


def test_m06_token_reaproveitado(monkeypatch):
    chamadas = {"token": 0}

    def handler(request):
        if request.url.path.endswith("/oauth/token"):
            chamadas["token"] += 1
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(200, json={"d": {"results": []}})

    conn = _odata(monkeypatch, handler)
    conn.fetch("MSG1")
    conn.fetch("MSG2")
    assert chamadas["token"] == 1


# ---------------------------------------------------------------------------
# M-08: PO "ALL" sem filtro de status
# ---------------------------------------------------------------------------


def test_m08_po_all_nao_filtra_por_failed(monkeypatch):
    from app.connectors.po_connector import POConnector

    monkeypatch.setattr(
        "app.connectors.po_connector.settings",
        Settings(po_base_url="https://po.example.com", po_username="u", po_password="p"),
    )
    params = []

    def handler(request):
        params.append(dict(request.url.params))
        return httpx.Response(200, json={"messages": []})

    conn = POConnector(client=httpx.Client(transport=httpx.MockTransport(handler)))
    conn.fetch("ALL")
    assert "status" not in params[-1]


# ---------------------------------------------------------------------------
# M-09: Workday sem URL base absoluta; token no host da API
# ---------------------------------------------------------------------------


def test_m09_workday_sem_base_url_e_erro_de_configuracao(monkeypatch):
    from app.connectors.workday_connector import WorkdayConnector

    monkeypatch.setattr(
        "app.connectors.workday_connector.settings",
        Settings(workday_tenant="acme", workday_rest_base_url=""),
    )

    def handler(request):  # pragma: no cover - nao pode ser chamado
        raise AssertionError("nao deveria ir a rede")

    conn = WorkdayConnector(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert conn.fetch("EVT-1").error_code == "CONFIGURATION_ERROR"


def test_m09_workday_token_no_host_da_api(monkeypatch):
    from app.connectors import workday_connector

    monkeypatch.setattr(
        "app.connectors.workday_connector.settings",
        Settings(
            workday_tenant="acme",
            workday_rest_base_url="https://wd2-impl-services1.workday.com/ccx/api/v1/acme",
        ),
    )
    assert workday_connector._token_url() == (
        "https://wd2-impl-services1.workday.com/ccx/oauth2/acme/token"
    )


# ---------------------------------------------------------------------------
# M-10: RFC - excecoes do pyrfc, breaker e timeout
# ---------------------------------------------------------------------------


def _fake_pyrfc(monkeypatch, comportamento):
    mod = types.ModuleType("pyrfc")
    for nome in ("CommunicationError", "LogonError", "ABAPApplicationError"):
        setattr(mod, nome, type(nome, (Exception,), {"key": "", "message": ""}))
    chamadas = []

    class Connection:
        def __init__(self, **kw):
            pass

        def call(self, fn, options=None, **kw):
            chamadas.append(options)
            return comportamento(mod)

        def close(self):
            pass

    mod.Connection = Connection
    monkeypatch.setitem(sys.modules, "pyrfc", mod)
    monkeypatch.setattr("app.connectors.rfc_connector.pyrfc", mod)
    monkeypatch.setattr("app.connectors.rfc_connector.HAS_PYRFC", True)
    monkeypatch.setattr("app.connectors.rfc_connector.settings.sap_ashost", "sap.example.com")
    monkeypatch.setattr(
        "app.connectors.rfc_connector.settings.connector_circuit_failure_threshold", 2
    )
    monkeypatch.setattr(
        "app.connectors.base.settings",
        Settings(connector_circuit_failure_threshold=2, connector_circuit_cooldown_seconds=60),
    )
    return mod, chamadas


@pytest.mark.parametrize(
    ("excecao", "codigo"),
    [("LogonError", "RFC_LOGON_FAILURE"), ("ABAPApplicationError", "FU_NOT_FOUND")],
)
def test_m10_excecao_do_pyrfc_vira_resultado(monkeypatch, excecao, codigo):
    from app.connectors.rfc_connector import RFCConnector

    def comportamento(mod):
        exc = getattr(mod, excecao)("falhou")
        exc.key = "FU_NOT_FOUND" if excecao == "ABAPApplicationError" else ""
        raise exc

    _fake_pyrfc(monkeypatch, comportamento)
    result = RFCConnector().fetch("0000000000123456")
    assert result.error_code == codigo and result.is_fallback
    assert not connector_circuit_breaker.is_open("RFC", 60)


def test_m10_falha_de_comunicacao_abre_o_circuito_e_ha_timeout(monkeypatch):
    from app.connectors.rfc_connector import RFCConnector

    def comportamento(mod):
        raise mod.CommunicationError("gateway down")

    _, chamadas = _fake_pyrfc(monkeypatch, comportamento)
    conn = RFCConnector()
    assert conn.fetch("0000000000123456").error_code == "RFC_COMMUNICATION_FAILURE"
    conn.fetch("0000000000123456")
    assert conn.fetch("0000000000123456").error_code == "CIRCUIT_OPEN"
    assert chamadas[0] == {"timeout": 20}
