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
from app.connectors.base import (
    ConnectorResult,
    SAPConnector,
    TokenResponseError,
    circuit_breaker_guard,
    connector_circuit_breaker,
    json_or_error,
    oauth_token_cache,
    record_response_outcome,
    token_error_result,
    validate_identifier_charset,
)
from app.contracts.model import Contract
from app.contracts.odata import MetadataError, parse_odata_metadata
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
        # Validacao 2026-10-07 (M-06): token reutilizado ate expirar, em vez
        # de um POST ao IdP por diagnostico.
        return oauth_token_cache.get(
            (settings.odata_oauth_token_url, settings.odata_client_id),
            lambda: client.post(
                settings.odata_oauth_token_url,
                data={"grant_type": "client_credentials"},
                auth=(settings.odata_client_id, settings.odata_client_secret),
            ),
        )

    def fetch_contract(self) -> Contract | None:  # DA-52
        """Le o `$metadata` do serviço e devolve o contrato normalizado.

        Reusa o mesmo OAuth de `_fetch_real` de proposito: duplicar o
        token aqui criaria um segundo caminho de credencial, e o dia que um
        deles divergisse o detector leria o contrato com permissao
        diferente da do diagnostico -- que e' o tipo de bug que so aparece
        em producao.

        Devolve `None` (estado `nao_introspectavel` no detector) quando o
        conector esta em mock ou sem URL: nao ha contrato para ler, e
        inventar um aqui seria pior do que nao ter resposta.
        """
        if not (self.use_real or settings.odata_service_url):
            return None
        client = self._injected_client or httpx.Client(timeout=10.0)
        try:
            token = self._get_access_token(client)
            # URL crua: o conector usa `odata_service_url` como endpoint
            # final, entao o $metadata e' a raiz do servico + sufixo.
            metadata_url = f"{settings.odata_service_url.rstrip('/')}/$metadata"
            response = client.get(
                metadata_url,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/xml"},
            )
            response.raise_for_status()
            return parse_odata_metadata(response.text)
        except (httpx.HTTPError, MetadataError, KeyError, ValueError):
            # Vira `unverified` no detector (app/contracts/observe.py).
            # Engolir aqui e' seguro: o report carrega o motivo, e o
            # chamador decide. O que nao pode acontecer e' devolver um
            # contrato vazio "sem drift".
            return None
        finally:
            if self._injected_client is None:
                client.close()

    def _fetch_real(self, identifier: str) -> ConnectorResult:
        if (blocked := circuit_breaker_guard("OData")) is not None:
            return blocked
        if (invalid := validate_identifier_charset(identifier, "OData")) is not None:
            return invalid
        client = self._injected_client or httpx.Client(timeout=10.0)
        try:
            token = self._get_access_token(client)
            response = client.get(
                settings.odata_service_url,
                params={"$filter": f"MessageId eq '{identifier}'", "$format": "json"},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except (httpx.HTTPStatusError, TokenResponseError) as exc:
            # M-06: 401/403 do token endpoint e credencial, nao indisponibilidade:
            # nao abre o circuito (so 5xx/429 contam).
            return token_error_result("OData", exc, "do CPI")
        except httpx.RequestError as exc:
            connector_circuit_breaker.record_failure(
                "OData",
                settings.connector_circuit_failure_threshold,
                settings.connector_circuit_cooldown_seconds,
            )
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

        record_response_outcome("OData", response.status_code)

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

        payload, invalid = json_or_error(response, "OData")
        if invalid is not None:
            return invalid
        results = payload.get("d", {}).get("results", [])
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
