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
        `docs/a2a-interoperability-layer.md` se/quando avaliado); misturar os dois agora
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
    _REGISTRY,
    connector_status,
)
from app.exceptions import DiagnosisOverloadedError
from app.mcp.policy import enforce
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
    salesforce, workday, ariba, successfactors, po, cap, apim. identifier e
    o nome do iFlow, RFC destination, numero de IDoc/incidente/case/evento/
    PO/funcionario, conforme o interface_type. A lista completa, com o modo
    (real/mock) de cada um, vem de `list_connectors`.
    """
    # DA-27: Capability Registry + Agent Execution Policy - fail-closed,
    # ver app/mcp/policy.py. Hoje sempre permite (tool read-only, scope
    # padrao ja cobre), mas garante que NENHUMA tool roda sem passar
    # por aqui primeiro, inclusive futuras tools de escrita.
    policy = enforce("diagnose_incident")
    request = IncidentRequest(
        description=description,
        logs=logs,
        payload=payload,
        interface_type=interface_type,
        identifier=identifier,
    )
    # M-27: timeout_seconds/max_retries da ToolPolicy existiam e nunca eram
    # aplicados. Timeout vira o teto do grafo (nunca acima de
    # DIAGNOSIS_TIMEOUT_SECONDS); retry so em sobrecarga (503 do semaforo),
    # que e o unico caso em que repetir e seguro - repetir um timeout
    # dobraria a carga do pipeline que ja nao deu conta.
    attempts = policy.max_retries + 1
    for attempt in range(attempts):
        try:
            response = run_diagnosis(request, timeout_seconds=policy.timeout_seconds)
            return response.model_dump()
        except DiagnosisOverloadedError:
            if attempt == attempts - 1:
                raise
    raise AssertionError("inalcancavel")  # pragma: no cover


_VENDOR = {
    "odata": "SAP",
    "rfc": "SAP",
    "cap": "SAP",
    "apim": "SAP",
    "po": "SAP",
    "successfactors": "SAP SuccessFactors",
    "ariba": "SAP Ariba",
    "servicenow": "não-SAP",
    "salesforce": "não-SAP",
    "workday": "não-SAP",
}


@mcp.tool()
def list_connectors() -> list[dict]:
    """Lista os conectores de sistemas externos disponiveis (SAP e nao-SAP)
    e, para cada um, se esta configurado para chamada REAL ou opera em modo
    demo/mock - sem fazer nenhuma chamada de rede (so inspeciona config).
    Util para um agente cliente decidir quais identificadores/cenarios
    testar antes de chamar `diagnose_incident`.

    Validacao 2026-10-07 (M-27): o catalogo era uma lista mantida a mao com
    8 conectores (faltavam successfactors e po) e dizia "RFC sempre mock"
    mesmo com SAP_ASHOST configurado. Agora vem do registro de conectores
    (`app.connectors._REGISTRY`) e de `connector_status()` - a mesma fonte
    de GET /health.
    """
    enforce("list_connectors")
    catalog = []
    for interface_type, state in connector_status().items():
        connector_cls = _REGISTRY[interface_type]
        catalog.append(
            {
                "interface_type": interface_type,
                "connector_class": connector_cls.__name__,
                "vendor": _VENDOR.get(interface_type, "não-SAP"),
                "real_data_configured": state["status"] == "real",
                "status": state["status"],
                "config_hint": state["note"],
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
