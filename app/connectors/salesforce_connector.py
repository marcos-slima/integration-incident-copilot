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
from app.connectors.base import (
    ConnectorResult,
    ExternalSystemConnector,
    TokenResponseError,
    circuit_breaker_guard,
    connector_circuit_breaker,
    json_or_error,
    oauth_token_cache,
    record_response_outcome,
    token_error_result,
    validate_identifier_charset,
)

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
        # Validacao 2026-10-07 (M-06): token reutilizado ate expirar, em vez
        # de um POST ao IdP por diagnostico.
        return oauth_token_cache.get(
            (
                f"{settings.salesforce_instance_url.rstrip('/')}/services/oauth2/token",
                settings.salesforce_client_id,
            ),
            lambda: client.post(
                f"{settings.salesforce_instance_url.rstrip('/')}/services/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.salesforce_client_id,
                    "client_secret": settings.salesforce_client_secret,
                },
            ),
        )

    def _fetch_real(self, identifier: str) -> ConnectorResult:
        if (blocked := circuit_breaker_guard("Salesforce")) is not None:
            return blocked
        if (invalid := validate_identifier_charset(identifier, "Salesforce")) is not None:
            return invalid
        client = self._injected_client or httpx.Client(timeout=self.timeout)
        # A REST API do Salesforce nao suporta bind variables no SOQL — a query
        # e sempre interpolada. validate_identifier_charset (acima) bloqueia
        # caracteres perigosos, mas escapamos apostrofos explicitamente como
        # defesa em profundidade (o escape canonico do SOQL e duplicar a apostrofe).
        safe_identifier = identifier.replace("'", "''")
        # SOQL nao aceita bind variables, entao a interpolacao e'
        # inevitavel. O valor nao e entrada do usuario cru: `identifier` passou
        # por `validate_identifier_charset` (acima) e o apostrofo e' escapado
        # uma linha acima. Um nosec generico aqui nao diria nada; este diz poror
        # que a string e' segura.
        soql = (
            "SELECT CaseNumber, Priority, Subject, Status, Origin FROM Case "
            f"WHERE CaseNumber = '{safe_identifier}' LIMIT 1"  # nosec B608
        )
        try:
            token = self._get_access_token(client)
            response = client.get(
                f"{settings.salesforce_instance_url.rstrip('/')}"
                f"/services/data/{settings.salesforce_api_version}/query",
                params={"q": soql},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except (httpx.HTTPStatusError, TokenResponseError) as exc:
            # M-06: 401/403 do token endpoint e credencial, nao indisponibilidade:
            # nao abre o circuito (so 5xx/429 contam).
            return token_error_result("Salesforce", exc, "do Salesforce")
        except httpx.RequestError as exc:
            connector_circuit_breaker.record_failure(
                "Salesforce",
                settings.connector_circuit_failure_threshold,
                settings.connector_circuit_cooldown_seconds,
            )
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

        record_response_outcome("Salesforce", response.status_code)

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

        payload, invalid = json_or_error(response, "Salesforce")
        if invalid is not None:
            return invalid
        records = payload.get("records", [])
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
