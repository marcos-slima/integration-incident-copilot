"""Conector SAP SuccessFactors Employee Central (EC) - representa o cenario
de referencia SuccessFactors<->S/4HANA (replicacao de funcionario falhando
por divergencia de dados entre sistemas de RH e ERP).

Mesmo criterio dos demais conectores reais opcionais:
`SFSF_BASE_URL` vazio (default) = modo demo/mock; preenchido = OAuth2
Client Credentials Grant (SAP BTP, Identity Authentication) + consulta
OData v2 ao status do funcionario em EC.

Endpoint real: GET /odata/v2/PerPerson('{id}') (chave = personIdExternal)
  com $expand=employmentNav/jobInfoNav.

Validacao 2026-10-07 (M-07): antes a chamada era
`/PerPerson?personIdExternal='x'`. No OData v2 isso e um parametro de query
desconhecido, NAO um filtro - o servico devolve a colecao inteira e o codigo
pegava `results[0]`, ou seja, o diagnostico podia usar os dados de OUTRO
funcionario. Tambem lia `replicationStatus` e `employmentStatus`, campos que
nao existem em PerPerson/EmpEmployment. Agora:
  - busca pela chave (um registro ou 404), e confere o personIdExternal
    devolvido;
  - o status de emprego vem de EmpEmployment.endDate (preenchido =
    desligado);
  - o estado da replicacao EC -> ERP NAO e exposto por PerPerson (fica no
    Data Replication Monitor); este conector nao o le e diz isso na
    mensagem, em vez de inventar.

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
        # Validacao 2026-10-07 (M-06): token reutilizado ate expirar, em vez
        # de um POST ao IdP por diagnostico.
        return oauth_token_cache.get(
            (settings.sfsf_oauth_token_url, settings.sfsf_client_id),
            lambda: client.post(
                settings.sfsf_oauth_token_url,
                data={"grant_type": "client_credentials"},
                auth=(settings.sfsf_client_id, settings.sfsf_client_secret),
            ),
        )

    def _fetch_real(self, identifier: str) -> ConnectorResult:
        if (blocked := circuit_breaker_guard("SuccessFactors")) is not None:
            return blocked
        if (invalid := validate_identifier_charset(identifier, "SuccessFactors")) is not None:
            return invalid

        client = self._injected_client or httpx.Client(timeout=self.timeout)
        try:
            token = self._get_access_token(client)
            # M-07: chave do registro, nunca parametro de query (que o OData
            # v2 ignora). O charset do identificador ja foi validado acima
            # (sem aspas), entao a interpolacao na chave e segura.
            response = client.get(
                f"{settings.sfsf_base_url.rstrip('/')}/odata/v2/PerPerson('{identifier}')",
                params={"$expand": "employmentNav/jobInfoNav", "$format": "json"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
        except (httpx.HTTPStatusError, TokenResponseError) as exc:
            # M-06: 401/403 do token endpoint e credencial, nao indisponibilidade:
            # nao abre o circuito (so 5xx/429 contam).
            return token_error_result("SuccessFactors", exc, "do SuccessFactors")
        except httpx.RequestError as exc:
            connector_circuit_breaker.record_failure(
                "SuccessFactors",
                settings.connector_circuit_failure_threshold,
                settings.connector_circuit_cooldown_seconds,
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

        record_response_outcome("SuccessFactors", response.status_code)

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

        payload, invalid = json_or_error(response, "SuccessFactors")
        if invalid is not None:
            return invalid
        record = payload.get("d") if isinstance(payload.get("d"), dict) else None
        if not record or "results" in record:
            # Leitura por chave devolve UM objeto. Lista aqui = API/versao
            # diferente da esperada; nao escolher "o primeiro" (M-07).
            return ConnectorResult(
                source_system="SuccessFactors",
                status="error",
                error_code="INVALID_RESPONSE",
                message="SuccessFactors nao devolveu um unico PerPerson para a chave pedida",
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )
        returned_id = record.get("personIdExternal")
        if returned_id is not None and str(returned_id) != identifier:
            return ConnectorResult(
                source_system="SuccessFactors",
                status="error",
                error_code="WRONG_RECORD",
                message=(
                    f"SuccessFactors devolveu o funcionario '{returned_id}' para a chave "
                    f"'{identifier}' - resultado descartado"
                ),
                raw="",
                is_mock=False,
                is_fallback=True,
            )

        employment = record.get("employmentNav") or {}
        if isinstance(employment, dict) and "results" in employment:
            employments = employment["results"] or []
            # Mais de um vinculo (recontratacao, global assignment): vale o
            # ultimo sem data de fim, se houver.
            open_ones = [e for e in employments if isinstance(e, dict) and not e.get("endDate")]
            employment = (open_ones or employments or [{}])[-1]
        end_date = employment.get("endDate") if isinstance(employment, dict) else None
        active = bool(employment) and not end_date
        emp_status = "active" if active else ("terminated" if end_date else "unknown")

        return ConnectorResult(
            source_system="SuccessFactors",
            status="ok" if active else "error",
            error_code="" if active else emp_status.upper(),
            message=(
                f"Funcionario '{identifier}' - vinculo: {emp_status}"
                + (f" (endDate={end_date})" if end_date else "")
                + ". Estado da replicacao EC->ERP nao e lido por este conector "
                "(Data Replication Monitor)."
            ),
            raw=str(record)[:2000],
            is_mock=False,
        )
