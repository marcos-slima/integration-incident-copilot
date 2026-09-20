"""Servidor MCP (Model Context Protocol) do SAP Integration Copilot - DA-19.

Terceiro item do roadmap "Projeto evolucao planejada" (apos AI Gateway
minimo/Evidence Layer e fechamento de autenticacao do A2A): MCP como
CONTRATO DE CAPABILITIES, nao mais um protocolo isolado de "conectar
um LLM a uma ferramenta" - a especificacao 2026 caminha para stateless
scaling, cache de capability catalog e autorizacao empresarial, o que
aproxima MCP de infraestrutura de producao (ver blog.modelcontextprotocol.io,
post 2026-07-28). Este modulo expoe o Copilot como SERVIDOR MCP (nao
cliente) - decisao explicita, nao a unica leitura possivel de "MCP,
conectores por dominio":

    (a) Copilot como MCP SERVER (escolhido aqui) - outros agentes/
        clientes MCP chamam `diagnose_incident`/`list_connectors` via
        protocolo MCP, reusando 100% da orquestracao LangGraph
        existente. Zero reescrita de connectors.
    (b) Copilot como MCP CLIENT consumindo conectores externos via MCP
        - migraria os `app/connectors/*` para servidores MCP de
        terceiros/proprios. Fica para uma fase seguinte (ver
        `docs/proposals/` se/quando avaliado); misturar os dois agora
        seria escopo maior que o necessario para o item do roadmap.

"Leitura primeiro" (conforme o roadmap): as ferramentas expostas aqui
sao estritamente de CONSULTA - `diagnose_incident` roda o mesmo
pipeline read-only usado por `/diagnose` e `/a2a` (RAG + conectores
mock/reais leem dados, nao escrevem em sistema nenhum; a UNICA escrita
possivel no processo, o grafo de incidentes historicos no Neo4j via
GraphRAG, so acontece se `GRAPH_RAG_ENABLED=true`, e ja e opt-in e
rotulada por proveniencia - ver DA-16/17). Nenhuma ferramenta aqui cria,
fecha ou altera chamados/tickets em sistema externo algum - isso ficaria
para uma fase de "MCP client com escrita", deliberadamente fora deste
escopo.

Autenticacao: reusa `settings.api_key` (o MESMO X-API-Key de
`/diagnose`, DA-18) via um middleware ASGI simples em
`RequireApiKeyMiddleware` abaixo. O SDK `mcp` oferece `AuthSettings`/
`TokenVerifier` (OAuth2) para cenarios enterprise reais, mas isso seria
sobre-engenharia para um laboratorio local-first que ja usa API Key
estatica em todo o resto (REST e A2A) - manter UM mecanismo de
autenticacao consistente em toda a superficie HTTP, nao dois.
"""

from __future__ import annotations

import secrets

from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.agent.graph import run_diagnosis
from app.config import settings
from app.connectors import (
    APIManagementConnector,
    AribaConnector,
    CAPConnector,
    ODataConnector,
    RFCConnector,
    SalesforceConnector,
    ServiceNowConnector,
    WorkdayConnector,
)
from app.models import IncidentRequest

mcp = MCPServer(
    name="sap-integration-copilot",
    version="0.1.0",
    instructions=(
        "Ferramentas de diagnostico de incidentes de integracao SAP/multi-vendor "
        "(OData/CPI, RFC/IDoc, ServiceNow, Salesforce, Workday, SAP Ariba, CAP, "
        "API Management). Todas as ferramentas sao read-only - nao criam, fecham "
        "nem alteram chamados em nenhum sistema externo."
    ),
)


@mcp.tool()
def diagnose_incident(
    description: str,
    logs: str | None = None,
    payload: str | None = None,
    interface_type: str | None = None,
    identifier: str | None = None,
) -> dict:
    """Diagnostica um incidente de integracao SAP/multi-vendor: correlaciona
    dados de conector (OData/CPI, RFC/IDoc, ServiceNow, Salesforce, Workday,
    SAP Ariba, CAP, API Management) com uma base de conhecimento via RAG e
    retorna causa raiz provavel, nivel de confianca, evidence_strength
    (sinal objetivo de fundamentacao - ver DA-15) e proximos passos.

    interface_type, se informado, deve ser um de: odata, rfc, servicenow,
    salesforce, workday, ariba, cap, apim. identifier e o nome do iFlow,
    RFC destination, numero de IDoc/incidente/case/evento/PO, conforme o
    interface_type.
    """
    request = IncidentRequest(
        description=description,
        logs=logs,
        payload=payload,
        interface_type=interface_type,
        identifier=identifier,
    )
    response = run_diagnosis(request)
    return response.model_dump()


# Mapeamento interface_type -> (classe do conector, atributo de settings
# que, se preenchido, ativa a chamada REAL em vez de mock - ver docstring
# de app/connectors/base.py). RFC nao segue esse padrao (gate e o
# parametro `use_real=True` no construtor, nao presenca de settings -
# continua mock-only ate pyrfc + SAP NetWeaver RFC SDK estarem
# disponiveis, ver app/connectors/rfc_connector.py), por isso tratado a
# parte abaixo.
_CONNECTOR_CATALOG: dict[str, tuple[type, str | None, str]] = {
    "odata": (ODataConnector, "odata_service_url", "SAP"),
    "rfc": (RFCConnector, None, "SAP"),
    "servicenow": (ServiceNowConnector, "servicenow_instance_url", "não-SAP"),
    "salesforce": (SalesforceConnector, "salesforce_instance_url", "não-SAP"),
    "workday": (WorkdayConnector, "workday_tenant", "não-SAP"),
    "ariba": (AribaConnector, "ariba_base_url", "SAP Ariba"),
    "cap": (CAPConnector, "cap_service_url", "SAP"),
    "apim": (APIManagementConnector, "apim_analytics_url", "SAP"),
}


@mcp.tool()
def list_connectors() -> list[dict]:
    """Lista os conectores de sistemas externos disponiveis (SAP e nao-SAP)
    e, para cada um, se esta configurado para chamada REAL ou opera em modo
    demo/mock - sem fazer nenhuma chamada de rede (so inspeciona config).
    Util para um agente cliente decidir quais identificadores/cenarios
    testar antes de chamar `diagnose_incident`.
    """
    catalog = []
    for interface_type, (connector_cls, settings_attr, vendor) in _CONNECTOR_CATALOG.items():
        if interface_type == "rfc":
            real_data_configured = False
            config_hint = (
                "Sempre mock nesta API - modo real exige "
                "RFCConnector(use_real=True) + pyrfc + SAP NetWeaver RFC SDK "
                "instalados no processo (nao ha flag de .env)."
            )
        else:
            real_data_configured = bool(getattr(settings, settings_attr))
            config_hint = (
                "Configurado para dados reais."
                if real_data_configured
                else f"Modo demo/mock - configure '{settings_attr}' no .env para dados reais."
            )
        catalog.append(
            {
                "interface_type": interface_type,
                "connector_class": connector_cls.__name__,
                "vendor": vendor,
                "real_data_configured": real_data_configured,
                "config_hint": config_hint,
            }
        )
    return catalog


class RequireApiKeyMiddleware:
    """Middleware ASGI que exige o mesmo X-API-Key de `/diagnose` (DA-18)
    antes de repassar a requisicao ao app MCP montado. Necessario porque o
    app MCP e um sub-app Starlette puro montado via `app.mount()` em
    `app/main.py` - nao passa pelo dependency injection do FastAPI
    (`Depends`/`Security`), entao a checagem tem que acontecer aqui, no
    nivel ASGI, igual ao padrao ja usado por `_check_auth` em
    `app/a2a/server.py`."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        api_key = request.headers.get("x-api-key")
        if not secrets.compare_digest(api_key or "", settings.api_key):
            response = JSONResponse({"error": "X-API-Key invalida ou ausente"}, status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def build_mcp_asgi_app() -> Starlette:
    """App ASGI do servidor MCP, pronto para `app.mount("/mcp", ...)` em
    `app/main.py`, ja com a checagem de X-API-Key aplicada."""
    inner = mcp.streamable_http_app(streamable_http_path="/")
    inner.add_middleware(RequireApiKeyMiddleware)
    return inner
