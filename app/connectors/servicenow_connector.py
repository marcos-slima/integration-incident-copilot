"""Conector ServiceNow - integra o Copilot com ITSM nao-SAP.

Diferente dos conectores SAP (OData/RFC, ainda mocks), este conector
faz chamadas HTTP REAIS contra a REST Table API do ServiceNow
(`/api/now/table/incident`) quando `SERVICENOW_INSTANCE_URL` esta
configurado no .env. Cai em modo demo (mock) apenas na AUSENCIA dessa
configuracao - pelo mesmo motivo que os conectores SAP mock existem
(prototipar/demonstrar sem depender de credenciais de um cliente
real), nao porque a chamada real nao esteja implementada aqui.

Por que ServiceNow, especificamente: e um dos 4 sistemas de referencia
adotados no portfolio (SuccessFactors/Workday, Salesforce, SAP Ariba,
ServiceNow) para representar cenarios de integracao multi-vendor sem
usar nomes de clientes reais. O cenario de referencia é um alerta de
monitoramento aberto no ServiceNow apontando uma falha do lado SAP
antes mesmo de alguem abrir chamado no lado SAP - a correlacao SAP +
nao-SAP que motiva a Decisao de Arquitetura #11 no README.

Uso:
    from app.connectors.servicenow_connector import ServiceNowConnector
    result = ServiceNowConnector().fetch("INC0010001")
"""

import httpx

from app.config import settings
from app.connectors.base import ConnectorResult, ExternalSystemConnector

_MOCK_SCENARIOS: dict[str, ConnectorResult] = {
    "INC0010001": ConnectorResult(
        source_system="ServiceNow",
        status="error",
        error_code="1 - Critical",
        message=(
            "Incidente ITSM: alerta de monitoramento aponta RFC destination " "indisponivel no SAP"
        ),
        raw=(
            "number=INC0010001\n"
            "priority=1 - Critical\n"
            "short_description=Monitoring alert: SAP RFC destination DEST_PRD unreachable\n"
            "category=Integration\n"
            "cmdb_ci=SAP ECC PRD"
        ),
    ),
}

_DEFAULT = ConnectorResult(
    source_system="ServiceNow",
    status="error",
    error_code="404",
    message="Incidente nao encontrado (modo demo - identificador nao reconhecido)",
    raw="ServiceNow Table API: nenhum registro para o numero informado (dados mock)",
    is_fallback=True,
)


class ServiceNowConnector(ExternalSystemConnector):
    """`fetch(identifier)` busca por numero de incidente (ex: 'INC0010001').

    `client`: injecao opcional de um `httpx.Client` ja configurado -
    usado pelos testes para injetar um `MockTransport` e exercitar o
    caminho HTTP real sem depender de uma instancia ServiceNow de
    verdade. Em producao, deixe `None` (um client novo e aberto/fechado
    a cada chamada).
    """

    def __init__(self, timeout: float = 10.0, client: httpx.Client | None = None):
        self.timeout = timeout
        self._injected_client = client

    def fetch(self, identifier: str) -> ConnectorResult:
        if not settings.servicenow_instance_url:
            return _MOCK_SCENARIOS.get(identifier, _DEFAULT)
        return self._fetch_real(identifier)

    def _fetch_real(self, identifier: str) -> ConnectorResult:
        client = self._injected_client or httpx.Client(timeout=self.timeout)
        try:
            response = client.get(
                f"{settings.servicenow_instance_url.rstrip('/')}/api/now/table/incident",
                params={
                    "sysparm_query": f"number={identifier}",
                    "sysparm_limit": "1",
                    "sysparm_fields": "number,priority,short_description,category,cmdb_ci",
                },
                auth=(settings.servicenow_username, settings.servicenow_password),
                headers={"Accept": "application/json"},
            )
        except httpx.RequestError as exc:
            return ConnectorResult(
                source_system="ServiceNow",
                status="error",
                error_code="CONNECTION_ERROR",
                message=f"Falha de rede ao consultar ServiceNow: {exc}",
                raw=str(exc),
                is_mock=False,
                is_fallback=True,
            )
        finally:
            if self._injected_client is None:
                client.close()

        if response.status_code != 200:
            return ConnectorResult(
                source_system="ServiceNow",
                status="error",
                error_code=str(response.status_code),
                message=f"ServiceNow Table API retornou HTTP {response.status_code}",
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )

        records = response.json().get("result", [])
        if not records:
            return ConnectorResult(
                source_system="ServiceNow",
                status="error",
                error_code="404",
                message=f"Nenhum incidente ServiceNow encontrado para '{identifier}'",
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )

        record = records[0]
        return ConnectorResult(
            source_system="ServiceNow",
            status="ok",
            error_code=record.get("priority"),
            message=record.get("short_description", ""),
            raw=str(record),
            is_mock=False,
        )
