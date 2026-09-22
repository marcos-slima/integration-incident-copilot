"""Conector SAP SuccessFactors Employee Central (EC) - representa o cenario
de referencia SuccessFactors<->S/4HANA (replicacao de funcionario falhando
por divergencia de dados entre sistemas de RH e ERP).

Mesmo criterio dos demais conectores reais opcionais:
`SFSF_BASE_URL` vazio (default) = modo demo/mock; preenchido = OAuth2
Client Credentials Grant (SAP BTP, Identity Authentication) + consulta
OData v2 ao status do funcionario em EC.

Endpoint real: GET /odata/v2/PerPerson(personIdExternal='{id}')
  com $expand=personalInfoNav,employmentNav,jobInfoNav para obter o
  estado atual do funcionario (active/inactive, jobCode, costCenter).

Nao testado contra um tenant SuccessFactors real (sem acesso disponivel)
- testado com `httpx.MockTransport`, mesma ressalva dos demais conectores
reais nao verificados contra sistema de producao.

Uso:
    from app.connectors.successfactors_connector import SuccessFactorsConnector
    result = SuccessFactorsConnector().fetch("SFSF-REPL-FAIL-DEMO")
"""

import httpx

from app.config import settings
from app.connectors.base import (
    ConnectorResult,
    ExternalSystemConnector,
    circuit_breaker_guard,
    connector_circuit_breaker,
    validate_identifier_charset,
)

_MOCK_SCENARIOS: dict[str, ConnectorResult] = {
    "SFSF-REPL-FAIL-DEMO": ConnectorResult(
        source_system="SuccessFactors",
        status="error",
        error_code="REPLICATION_FAILED",
        message=(
            "Replicacao de funcionario bloqueada: costCenter '4710' nao existe "
            "no S/4HANA (PA30/IT0001) — dados enviados pelo EC via MDI nao "
            "foram aceitos pelo HCM/Payroll"
        ),
        raw=(
            "personIdExternal=SFSF-REPL-FAIL-DEMO\n"
            "employmentStatus=active\n"
            "replicationStatus=FAILED\n"
            "errorCode=REPLICATION_FAILED\n"
            "detail=Cost center '4710' not found in S/4HANA — last successful "
            "replication 3 days ago via SAP Master Data Integration (MDI)"
        ),
    ),
    "SFSF-INACTIVE-DEMO": ConnectorResult(
        source_system="SuccessFactors",
        status="error",
        error_code="EMPLOYEE_INACTIVE",
        message=(
            "Funcionario inativo no SuccessFactors EC: evento de desligamento "
            "processado mas replicacao de termination para o S/4HANA pendente "
            "(bloqueio de integracao MDI ativo)"
        ),
        raw=(
            "personIdExternal=SFSF-INACTIVE-DEMO\n"
            "employmentStatus=inactive\n"
            "terminationDate=2026-08-31\n"
            "replicationStatus=PENDING\n"
            "detail=Termination event not yet replicated to S/4HANA PA — "
            "MDI queue depth: 14 items pending"
        ),
    ),
    "SFSF-AUTH-FAIL-DEMO": ConnectorResult(
        source_system="SuccessFactors",
        status="error",
        error_code="401",
        message=(
            "Falha de autenticacao OAuth2 no SuccessFactors: token expirado "
            "ou client_id invalido — verificar configuracao no BTP Cockpit "
            "(Identity Authentication / OAuth2 Client)"
        ),
        raw=(
            "OData v2 API response: HTTP 401 Unauthorized\n"
            'WWW-Authenticate: Bearer realm="SuccessFactors"\n'
            "detail=OAuth2 token expired or client not authorized for "
            "SuccessFactors OData v2 scope"
        ),
    ),
}

_DEFAULT = ConnectorResult(
    source_system="SuccessFactors",
    status="error",
    error_code="404",
    message="Funcionario nao encontrado (modo demo - identificador nao reconhecido)",
    raw=(
        "SuccessFactors OData v2 API: nenhum registro PerPerson para o "
        "identificador informado (dados mock)"
    ),
    is_fallback=True,
)


class SuccessFactorsConnector(ExternalSystemConnector):
    """`fetch(identifier)` busca por personIdExternal no SuccessFactors EC.

    `client`: injecao opcional de `httpx.Client`, mesmo padrao dos
    demais conectores reais — usado pelos testes para simular a API
    OData v2 do SuccessFactors via `httpx.MockTransport`.
    """

    def __init__(self, timeout: float = 10.0, client: httpx.Client | None = None):
        self.timeout = timeout
        self._injected_client = client

    def fetch(self, identifier: str) -> ConnectorResult:
        if not settings.sfsf_base_url:
            return _MOCK_SCENARIOS.get(identifier, _DEFAULT)
        return self._fetch_real(identifier)

    def _get_access_token(self, client: httpx.Client) -> str:
        response = client.post(
            settings.sfsf_oauth_token_url,
            data={"grant_type": "client_credentials"},
            auth=(settings.sfsf_client_id, settings.sfsf_client_secret),
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def _fetch_real(self, identifier: str) -> ConnectorResult:
        if (blocked := circuit_breaker_guard("SuccessFactors")) is not None:
            return blocked
        if (invalid := validate_identifier_charset(identifier, "SuccessFactors")) is not None:
            return invalid

        client = self._injected_client or httpx.Client(timeout=self.timeout)
        try:
            token = self._get_access_token(client)
            response = client.get(
                f"{settings.sfsf_base_url.rstrip('/')}/odata/v2/PerPerson",
                params={
                    "personIdExternal": f"'{identifier}'",
                    "$expand": "personalInfoNav,employmentNav,jobInfoNav",
                    "$format": "json",
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPStatusError as exc:
            connector_circuit_breaker.record_failure(
                "SuccessFactors", settings.connector_circuit_failure_threshold
            )
            return ConnectorResult(
                source_system="SuccessFactors",
                status="error",
                error_code=str(exc.response.status_code),
                message=(
                    f"Falha ao obter token OAuth2 do SuccessFactors: "
                    f"HTTP {exc.response.status_code}"
                ),
                raw=exc.response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )
        except httpx.RequestError as exc:
            connector_circuit_breaker.record_failure(
                "SuccessFactors", settings.connector_circuit_failure_threshold
            )
            return ConnectorResult(
                source_system="SuccessFactors",
                status="error",
                error_code="CONNECTION_ERROR",
                message=f"Falha de rede ao consultar SuccessFactors EC: {exc}",
                raw=str(exc),
                is_mock=False,
                is_fallback=True,
            )
        finally:
            if self._injected_client is None:
                client.close()

        connector_circuit_breaker.record_success("SuccessFactors")

        if response.status_code == 401:
            return ConnectorResult(
                source_system="SuccessFactors",
                status="error",
                error_code="401",
                message="Token OAuth2 invalido ou expirado — verificar client_id/secret no BTP",
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )
        if response.status_code == 404:
            return ConnectorResult(
                source_system="SuccessFactors",
                status="error",
                error_code="404",
                message=f"Funcionario '{identifier}' nao encontrado no SuccessFactors EC",
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )
        if response.status_code != 200:
            return ConnectorResult(
                source_system="SuccessFactors",
                status="error",
                error_code=str(response.status_code),
                message=f"SuccessFactors OData v2 retornou HTTP {response.status_code}",
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )

        data = response.json()
        # OData v2 envolve resultado em d.results (lista) ou d (objeto unico)
        record = data.get("d", data)
        if isinstance(record, dict) and "results" in record:
            results = record["results"]
            record = results[0] if results else {}

        employment = record.get("employmentNav") or {}
        if isinstance(employment, dict) and "results" in employment:
            emp_list = employment["results"]
            employment = emp_list[0] if emp_list else {}

        emp_status = employment.get("employmentStatus", "unknown")
        repl_status = record.get("replicationStatus", "")

        if repl_status == "FAILED":
            return ConnectorResult(
                source_system="SuccessFactors",
                status="error",
                error_code="REPLICATION_FAILED",
                message=(
                    f"Replicacao do funcionario '{identifier}' falhou no MDI "
                    f"(employmentStatus={emp_status})"
                ),
                raw=str(record),
                is_mock=False,
            )

        return ConnectorResult(
            source_system="SuccessFactors",
            status="ok" if emp_status == "active" else "error",
            error_code=emp_status if emp_status != "active" else "",
            message=(
                f"Funcionario '{identifier}' — status: {emp_status}"
                + (f", replicacao: {repl_status}" if repl_status else "")
            ),
            raw=str(record),
            is_mock=False,
        )
