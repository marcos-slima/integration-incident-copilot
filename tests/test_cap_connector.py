"""Testes do CAPConnector - mesmo padrao dos demais conectores reais
(httpx.MockTransport simulando token XSUAA + consulta OData v4)."""

import httpx

from app.connectors.cap_connector import CAPConnector


def test_cap_connector_mock_scenario_when_not_configured(monkeypatch):
    monkeypatch.setattr("app.connectors.cap_connector.settings.cap_service_url", "")
    connector = CAPConnector()
    result = connector.fetch("CAP-PO-APPROVAL-DEMO")

    assert result.is_mock is True
    assert result.error_code == "422"
    assert "aprovacao" in result.message.lower() or "approver" in result.raw.lower()


def test_cap_connector_unknown_identifier_returns_safe_fallback(monkeypatch):
    monkeypatch.setattr("app.connectors.cap_connector.settings.cap_service_url", "")
    connector = CAPConnector()
    result = connector.fetch("ID-QUE-NAO-EXISTE")

    assert result.is_fallback is True
    assert result.error_code == "404"


def test_cap_connector_real_path_success(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.cap_connector.settings.cap_service_url", "https://fake.cap/odata/v4/svc"
    )
    monkeypatch.setattr(
        "app.connectors.cap_connector.settings.cap_xsuaa_token_url",
        "https://fake.xsuaa/oauth/token",
    )
    monkeypatch.setattr("app.connectors.cap_connector.settings.cap_client_id", "fake-client")
    monkeypatch.setattr("app.connectors.cap_connector.settings.cap_client_secret", "fake-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "fake-token", "expires_in": 3600})
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "ID": "PO-APPROVAL-00042",
                        "status": "rejected",
                        "message": "campo obrigatorio ausente",
                    }
                ]
            },
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = CAPConnector(client=mock_client)

    result = connector.fetch("PO-APPROVAL-00042")

    assert result.is_mock is False
    assert result.status == "ok"
    assert "campo obrigatorio" in result.message


def test_cap_connector_real_path_token_failure(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.cap_connector.settings.cap_service_url", "https://fake.cap/odata/v4/svc"
    )
    monkeypatch.setattr(
        "app.connectors.cap_connector.settings.cap_xsuaa_token_url",
        "https://fake.xsuaa/oauth/token",
    )
    monkeypatch.setattr("app.connectors.cap_connector.settings.cap_client_id", "fake-client")
    monkeypatch.setattr("app.connectors.cap_connector.settings.cap_client_secret", "wrong-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client"})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = CAPConnector(client=mock_client)

    result = connector.fetch("PO-APPROVAL-00042")

    assert result.is_mock is False
    assert result.is_fallback is True
    assert result.error_code == "401"
