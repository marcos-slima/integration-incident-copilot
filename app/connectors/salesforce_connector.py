"""Conector Salesforce - representa o cenario de referencia
Salesforce<->SAP (ex: Case aberto no Service Cloud apontando falha na
sincronizacao de um pedido de venda com o SAP).

Mesmo criterio dos demais conectores reais opcionais (ServiceNow,
OData): `SALESFORCE_INSTANCE_URL` vazio (default) = modo demo/mock;
preenchido = OAuth2 Client Credentials Flow (Connected App) + consulta
SOQL via REST API.

Nao testado contra uma org Salesforce real (sem sandbox disponivel) -
testado com `httpx.MockTransport` simulando as duas chamadas (token +
query), mesma ressalva de `ODataConnector`/`RFCConnector`.

Uso:
    from app.connectors.salesforce_connector import SalesforceConnector
    result = SalesforceConnector().fetch("500XX0000ABCDE")  # Case Id/Number
"""

import httpx

from app.config import settings
from app.connectors.base import ConnectorResult, ExternalSystemConnector

_MOCK_SCENARIOS: dict[str, ConnectorResult] = {
    "SF-CASE-00847-DEMO": ConnectorResult(
        source_system="Salesforce",
        status="error",
        error_code="High",
        message=(
            "Case Salesforce: cliente reporta pedido de venda nao refletido no "
            "SAP apos 2 dias (falha silenciosa na integracao Salesforce->SAP)"
        ),
        raw=(
            "CaseNumber=00847\n"
            "Priority=High\n"
            "Subject=Pedido criado no Salesforce nao aparece no SAP SD\n"
            "Status=Working\n"
            "Origin=Integration Middleware Alert"
        ),
    ),
}

_DEFAULT = ConnectorResult(
    source_system="Salesforce",
    status="error",
    error_code="404",
    message="Case nao encontrado (modo demo - identificador nao reconhecido)",
    raw="Salesforce REST API: nenhum registro para o identificador informado (dados mock)",
    is_fallback=True,
)


class SalesforceConnector(ExternalSystemConnector):
    """`fetch(identifier)` busca por CaseNumber (ex: '00847').

    `client`: injecao opcional de `httpx.Client`, mesmo padrao de
    `ServiceNowConnector` - usado pelos testes para simular a org via
    `httpx.MockTransport` sem depender de uma org Salesforce real.
    """

    def __init__(self, timeout: float = 10.0, client: httpx.Client | None = None):
        self.timeout = timeout
        self._injected_client = client

    def fetch(self, identifier: str) -> ConnectorResult:
        if not settings.salesforce_instance_url:
            return _MOCK_SCENARIOS.get(identifier, _DEFAULT)
        return self._fetch_real(identifier)

    def _get_access_token(self, client: httpx.Client) -> str:
        response = client.post(
            f"{settings.salesforce_instance_url.rstrip('/')}/services/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": settings.salesforce_client_id,
                "client_secret": settings.salesforce_client_secret,
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def _fetch_real(self, identifier: str) -> ConnectorResult:
        client = self._injected_client or httpx.Client(timeout=self.timeout)
        soql = (
            f"SELECT CaseNumber, Priority, Subject, Status, Origin FROM Case "
            f"WHERE CaseNumber = '{identifier}' LIMIT 1"
        )
        try:
            token = self._get_access_token(client)
            response = client.get(
                f"{settings.salesforce_instance_url.rstrip('/')}"
                f"/services/data/{settings.salesforce_api_version}/query",
                params={"q": soql},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except httpx.HTTPStatusError as exc:
            return ConnectorResult(
                source_system="Salesforce",
                status="error",
                error_code=str(exc.response.status_code),
                message=f"Falha ao obter token OAuth2 do Salesforce: HTTP {exc.response.status_code}",
                raw=exc.response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )
        except httpx.RequestError as exc:
            return ConnectorResult(
                source_system="Salesforce",
                status="error",
                error_code="CONNECTION_ERROR",
                message=f"Falha de rede ao consultar Salesforce: {exc}",
                raw=str(exc),
                is_mock=False,
                is_fallback=True,
            )
        finally:
            if self._injected_client is None:
                client.close()

        if response.status_code != 200:
            return ConnectorResult(
                source_system="Salesforce",
                status="error",
                error_code=str(response.status_code),
                message=f"Salesforce REST API retornou HTTP {response.status_code}",
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )

        records = response.json().get("records", [])
        if not records:
            return ConnectorResult(
                source_system="Salesforce",
                status="error",
                error_code="404",
                message=f"Nenhum Case Salesforce encontrado para '{identifier}'",
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )

        record = records[0]
        return ConnectorResult(
            source_system="Salesforce",
            status="ok",
            error_code=record.get("Priority"),
            message=record.get("Subject", ""),
            raw=str(record),
            is_mock=False,
        )
