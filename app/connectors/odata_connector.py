"""Conector OData/CPI - modo demo (mock) por default, chamada HTTP
REAL quando configurado.

Mesmo criterio dos demais conectores "reais opcionais" deste projeto
(ServiceNow, RFC): a ausencia de configuracao (`ODATA_SERVICE_URL`
vazio) cai em modo demo; a presenca ativa o caminho real, sem precisar
mudar nenhum outro arquivo (LangGraph, RAG, API).

Fluxo real implementado (padrao comum de integracao via SAP CPI/
Integration Suite): OAuth2 Client Credentials Grant contra o token
endpoint, seguido de GET no servico OData (v2, formato `{"d": {...}}`)
filtrando pelo identificador (ex: nome do iFlow, ID da mensagem MPL).

Nao testado contra um SAP Integration Suite real (sem tenant
disponivel) - testado com `httpx.MockTransport` simulando as duas
chamadas (token + OData GET), o que exercita de fato o codigo de
producao (parsing de token, montagem de header Bearer, parsing do
payload OData v2), so sem uma rede/tenant real do outro lado. Mesma
ressalva que se aplica a `RFCConnector._fetch_real`.
"""

import httpx

from app.config import settings
from app.connectors.base import ConnectorResult, SAPConnector
from app.exceptions import ConfigurationError

_MOCK_SCENARIOS: dict[str, ConnectorResult] = {
    "CPI-401-DEMO": ConnectorResult(
        source_system="OData",
        status="error",
        error_code="401",
        message="Unauthorized ao chamar endpoint externo via iFlow CPI",
        raw=(
            "HTTP/1.1 401 Unauthorized\n"
            'WWW-Authenticate: Bearer error="invalid_token"\n'
            '{"error": "invalid_token", "error_description": "Access token expired"}'
        ),
    ),
    "CPI-TIMEOUT-DEMO": ConnectorResult(
        source_system="OData",
        status="error",
        error_code="504",
        message="Timeout ao consumir servico OData a partir de iFlow CPI",
        raw=(
            "HTTP/1.1 504 Gateway Timeout\n"
            "MPL Status: FAILED\n"
            "Adapter: OData V2, timeout apos 60000ms, query sem $filter/$top"
        ),
    ),
}

_DEFAULT = ConnectorResult(
    source_system="OData",
    status="error",
    error_code="500",
    message="Erro generico simulado ao consumir servico OData (identificador nao reconhecido)",
    raw="HTTP/1.1 500 Internal Server Error (dados mock, identificador desconhecido)",
    is_fallback=True,
)


class ODataConnector(SAPConnector):
    """`use_real=True` forca o caminho HTTP real mesmo sem
    `ODATA_SERVICE_URL` configurado - nesse caso falha alto e claro
    (`ConfigurationError`) em vez de silenciosamente cair em mock,
    mesmo padrao usado por `RFCConnector`."""

    def __init__(self, use_real: bool = False, client: httpx.Client | None = None):
        if use_real and not settings.odata_service_url:
            raise ConfigurationError(
                "ODataConnector(use_real=True) exige ODATA_SERVICE_URL (e "
                "ODATA_OAUTH_TOKEN_URL/ODATA_CLIENT_ID/ODATA_CLIENT_SECRET) "
                "configurados no .env - sem isso nao ha para onde chamar."
            )
        self.use_real = use_real
        self._injected_client = client

    def fetch(self, identifier: str) -> ConnectorResult:
        if self.use_real or settings.odata_service_url:
            return self._fetch_real(identifier)
        return _MOCK_SCENARIOS.get(identifier, _DEFAULT)

    def _get_access_token(self, client: httpx.Client) -> str:
        response = client.post(
            settings.odata_oauth_token_url,
            data={"grant_type": "client_credentials"},
            auth=(settings.odata_client_id, settings.odata_client_secret),
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def _fetch_real(self, identifier: str) -> ConnectorResult:
        client = self._injected_client or httpx.Client(timeout=10.0)
        try:
            token = self._get_access_token(client)
            response = client.get(
                settings.odata_service_url,
                params={"$filter": f"MessageId eq '{identifier}'", "$format": "json"},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except httpx.HTTPStatusError as exc:
            return ConnectorResult(
                source_system="OData",
                status="error",
                error_code=str(exc.response.status_code),
                message=f"Falha ao obter token OAuth2 do CPI: HTTP {exc.response.status_code}",
                raw=exc.response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )
        except httpx.RequestError as exc:
            return ConnectorResult(
                source_system="OData",
                status="error",
                error_code="CONNECTION_ERROR",
                message=f"Falha de rede ao consultar servico OData: {exc}",
                raw=str(exc),
                is_mock=False,
                is_fallback=True,
            )
        finally:
            if self._injected_client is None:
                client.close()

        if response.status_code != 200:
            return ConnectorResult(
                source_system="OData",
                status="error",
                error_code=str(response.status_code),
                message=f"Servico OData retornou HTTP {response.status_code}",
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )

        results = response.json().get("d", {}).get("results", [])
        if not results:
            return ConnectorResult(
                source_system="OData",
                status="error",
                error_code="404",
                message=f"Nenhuma mensagem OData encontrada para '{identifier}'",
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )

        record = results[0]
        status = record.get("Status", "UNKNOWN")
        return ConnectorResult(
            source_system="OData",
            status="ok" if status in {"COMPLETED", "PROCESSED"} else "error",
            error_code=status,
            message=record.get("StatusText", record.get("LogText", "")),
            raw=str(record),
            is_mock=False,
        )
