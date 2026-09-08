"""Testes do APIManagementConnector.

AVISO: valida a LOGICA do conector (parsing, guardrails, fallback),
nao o schema real da API - que e especulativo, ver docstring do
modulo app/connectors/apimanagement_connector.py.
"""

import httpx

from app.connectors.apimanagement_connector import APIManagementConnector


def test_apim_connector_mock_scenario_when_not_configured(monkeypatch):
    monkeypatch.setattr("app.connectors.apimanagement_connector.settings.apim_analytics_url", "")
    connector = APIManagementConnector()
    result = connector.fetch("APIM-RATE-LIMIT-DEMO")

    assert result.is_mock is True
    assert result.error_code == "429"


def test_apim_connector_unknown_identifier_returns_safe_fallback(monkeypatch):
    monkeypatch.setattr("app.connectors.apimanagement_connector.settings.apim_analytics_url", "")
    connector = APIManagementConnector()
    result = connector.fetch("ID-QUE-NAO-EXISTE")

    assert result.is_fallback is True
    assert result.error_code == "404"


def test_apim_connector_real_path_success(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.apimanagement_connector.settings.apim_analytics_url",
        "https://fake.apim/analytics",
    )
    monkeypatch.setattr(
        "app.connectors.apimanagement_connector.settings.apim_oauth_token_url",
        "https://fake.apim/oauth/token",
    )
    monkeypatch.setattr(
        "app.connectors.apimanagement_connector.settings.apim_client_id", "fake-client"
    )
    monkeypatch.setattr(
        "app.connectors.apimanagement_connector.settings.apim_client_secret", "fake-secret"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "fake-token"})
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "proxy": "proxy-pedidos-v2",
                        "statusCode": 429,
                        "violatedPolicyName": "RateLimit-100rpm",
                    }
                ]
            },
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = APIManagementConnector(client=mock_client)

    result = connector.fetch("proxy-pedidos-v2")

    assert result.is_mock is False
    assert result.status == "ok"
    assert "RateLimit" in result.message
