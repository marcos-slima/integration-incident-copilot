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

import httpx

from app.config import settings
from app.connectors.base import ConnectorResult, ExternalSystemConnector

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
        response = client.post(
            f"https://{settings.workday_tenant}.workday.com/ccx/oauth2/"
            f"{settings.workday_tenant}/token",
            data={"grant_type": "client_credentials"},
            auth=(settings.workday_client_id, settings.workday_client_secret),
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def _fetch_real(self, identifier: str) -> ConnectorResult:
        client = self._injected_client or httpx.Client(timeout=self.timeout)
        try:
            token = self._get_access_token(client)
            response = client.get(
                f"{settings.workday_rest_base_url.rstrip('/')}/integrationEvents/{identifier}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except httpx.HTTPStatusError as exc:
            return ConnectorResult(
                source_system="Workday",
                status="error",
                error_code=str(exc.response.status_code),
                message=f"Falha ao obter token OAuth2 do Workday: HTTP {exc.response.status_code}",
                raw=exc.response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )
        except httpx.RequestError as exc:
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

        record = response.json()
        status = record.get("status", "Unknown")
        return ConnectorResult(
            source_system="Workday",
            status="ok" if status in {"Completed", "Success"} else "error",
            error_code=status,
            message=record.get("errorMessage", record.get("description", "")),
            raw=str(record),
            is_mock=False,
        )
