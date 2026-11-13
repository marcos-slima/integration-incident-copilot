"""Conector Workday - representa o cenario de referencia
SuccessFactors<->Workday (replicacao de dados de funcionario entre
sistemas de RH de fornecedores diferentes).

Mesmo criterio dos demais conectores reais opcionais: `WORKDAY_TENANT`
vazio (default) = modo demo/mock; preenchido = OAuth2 Client
Credentials Grant + consulta REST.

Nao testado contra um tenant Workday real (sem acesso disponivel) -
testado com `httpx.MockTransport`, mesma ressalva dos demais
conectores reais nao verificados contra sistema de producao.

Uso:
    from app.connectors.workday_connector import WorkdayConnector
    result = WorkdayConnector().fetch("WD-SYNC-FAIL-DEMO")
"""

from urllib.parse import urlsplit

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
    "WD-SYNC-FAIL-DEMO": ConnectorResult(
        source_system="Workday",
        status="error",
        error_code="INTEGRATION_EVENT_ERROR",
        message=(
            "Evento de integracao Workday falhou ao replicar mudanca de cargo "
            "originada no SAP SuccessFactors Employee Central (EC->Workday)"
        ),
        raw=(
            "Integration_Event_ID=WD-SYNC-FAIL-DEMO\n"
            "Integration_System=SuccessFactors_EC_to_Workday\n"
            "Status=Error\n"
            "Error_Message=Worker_ID nao encontrado no Workday para o "
            "Person_ID_External recebido do SuccessFactors EC\n"
            "Completed_At=2026-08-30T14:12:00Z"
        ),
    ),
}

_DEFAULT = ConnectorResult(
    source_system="Workday",
    status="error",
    error_code="404",
    message="Evento de integracao nao encontrado (modo demo - identificador nao reconhecido)",
    raw="Workday REST API: nenhum registro para o identificador informado (dados mock)",
    is_fallback=True,
)


def _token_url() -> str:
    """M-09: token endpoint explicito ou derivado do host da REST API."""
    if settings.workday_token_url:
        return settings.workday_token_url
    host = urlsplit(settings.workday_rest_base_url).netloc
    return f"https://{host}/ccx/oauth2/{settings.workday_tenant}/token"


def _config_error() -> ConnectorResult | None:
    """Validacao 2026-10-07 (M-09): com WORKDAY_TENANT preenchido e
    WORKDAY_REST_BASE_URL vazio, o GET virava URL relativa
    ("/integrationEvents/<id>") e o httpx falhava com erro opaco. Agora a
    configuracao incompleta vira um resultado legivel, sem tentar a rede."""
    base = urlsplit(settings.workday_rest_base_url or "")
    if base.scheme != "https" or not base.netloc:
        return ConnectorResult(
            source_system="Workday",
            status="error",
            error_code="CONFIGURATION_ERROR",
            message=(
                "Workday em modo real (WORKDAY_TENANT preenchido) exige "
                "WORKDAY_REST_BASE_URL absoluta com https (ex.: "
                "https://wd2-impl-services1.workday.com/ccx/api/v1/<tenant>)."
            ),
            raw="",
            is_mock=False,
            is_fallback=True,
        )
    return None


class WorkdayConnector(ExternalSystemConnector):
    """`fetch(identifier)` busca por ID de evento de integracao.

    `client`: injecao opcional de `httpx.Client`, mesmo padrao dos
    demais conectores reais - usado pelos testes para simular o tenant
    via `httpx.MockTransport`.
    """

    def __init__(self, timeout: float = 10.0, client: httpx.Client | None = None):
        self.timeout = timeout
        self._injected_client = client

    def fetch(self, identifier: str) -> ConnectorResult:
        if not settings.workday_tenant:
            return _MOCK_SCENARIOS.get(identifier, _DEFAULT)
        return self._fetch_real(identifier)

    def _get_access_token(self, client: httpx.Client) -> str:
        # Validacao 2026-10-07 (M-06): token reutilizado ate expirar, em vez
        # de um POST ao IdP por diagnostico.
        return oauth_token_cache.get(
            (_token_url(), settings.workday_client_id),
            lambda: client.post(
                _token_url(),
                data={"grant_type": "client_credentials"},
                auth=(settings.workday_client_id, settings.workday_client_secret),
            ),
        )

    def _fetch_real(self, identifier: str) -> ConnectorResult:
        if (misconfigured := _config_error()) is not None:
            return misconfigured
        if (blocked := circuit_breaker_guard("Workday")) is not None:
            return blocked
        if (invalid := validate_identifier_charset(identifier, "Workday")) is not None:
            return invalid
        client = self._injected_client or httpx.Client(timeout=self.timeout)
        try:
            token = self._get_access_token(client)
            response = client.get(
                f"{settings.workday_rest_base_url.rstrip('/')}/integrationEvents/{identifier}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except (httpx.HTTPStatusError, TokenResponseError) as exc:
            # M-06: 401/403 do token endpoint e credencial, nao indisponibilidade:
            # nao abre o circuito (so 5xx/429 contam).
            return token_error_result("Workday", exc, "do Workday")
        except httpx.RequestError as exc:
            connector_circuit_breaker.record_failure(
                "Workday",
                settings.connector_circuit_failure_threshold,
                settings.connector_circuit_cooldown_seconds,
            )
            return ConnectorResult(
                source_system="Workday",
                status="error",
                error_code="CONNECTION_ERROR",
                message=f"Falha de rede ao consultar Workday: {exc}",
                raw=str(exc),
                is_mock=False,
                is_fallback=True,
            )
        finally:
            if self._injected_client is None:
                client.close()

        record_response_outcome("Workday", response.status_code)

        if response.status_code == 404:
            return ConnectorResult(
                source_system="Workday",
                status="error",
                error_code="404",
                message=f"Nenhum evento de integracao Workday encontrado para '{identifier}'",
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )
        if response.status_code != 200:
            return ConnectorResult(
                source_system="Workday",
                status="error",
                error_code=str(response.status_code),
                message=f"Workday REST API retornou HTTP {response.status_code}",
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )

        payload, invalid = json_or_error(response, "Workday")
        if invalid is not None:
            return invalid
        record = payload
        status = record.get("status", "Unknown")
        return ConnectorResult(
            source_system="Workday",
            status="ok" if status in {"Completed", "Success"} else "error",
            error_code=status,
            message=record.get("errorMessage", record.get("description", "")),
            raw=str(record),
            is_mock=False,
        )
