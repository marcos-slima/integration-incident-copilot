"""Interfaces de agentes publicadas pelo deploy do IIC."""

from app.sdk import (
    A2AConfig,
    AgentInterfaces,
    DiagnosisOverloadedError,
    MCPConfig,
    MCPTool,
    connector_catalog,
    incident_request,
    registered_tool_policy,
    run_diagnosis,
)

AGENT_CARD = {
    "name": "SAP Integration Copilot",
    "description": (
        "Agente de diagnostico de incidentes de integracao - correlaciona "
        "dados de sistemas SAP (OData/CPI, RFC/IDoc) e nao-SAP (ServiceNow, "
        "Salesforce, Workday, SAP Ariba) com uma base de conhecimento via RAG "
        "para propor causa raiz provavel e proximos passos."
    ),
    "version": "1.4.0",
    "capabilities": {
        "streaming": False,
        "pushNotifications": False,
    },
    "defaultInputModes": ["text/plain", "application/json"],
    "defaultOutputModes": ["text/markdown"],
    "skills": [
        {
            "id": "diagnose-integration-incident",
            "name": "Diagnosticar incidente de integracao SAP/multi-vendor",
            "description": (
                "Recebe a descricao de um incidente (texto livre e/ou "
                "identificador de interface - iFlow, RFC destination, numero "
                "de IDoc/incidente ServiceNow/Case Salesforce/evento Workday/"
                "PO Ariba) e retorna causa raiz provavel, nivel de confianca e "
                "proximos passos, em Markdown."
            ),
            "tags": [
                "sap",
                "integration",
                "incident-diagnosis",
                "odata",
                "rfc",
                "servicenow",
                "salesforce",
                "workday",
                "ariba",
            ],
            "examples": [
                "iFlow falhando com erro 401",
                "RFC destination indisponivel, IDoc parado em status 51",
                "Case Salesforce reporta pedido nao sincronizado com o SAP",
            ],
            "inputModes": ["text/plain", "application/json"],
            "outputModes": ["text/markdown"],
        }
    ],
}


def diagnose_incident(
    description: str,
    logs: str | None = None,
    payload: str | None = None,
    interface_type: str | None = None,
    identifier: str | None = None,
    locale: str | None = None,
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
    (real/mock) de cada um, vem de `list_connectors`. locale ('pt-BR' ou
    'en', padrao 'pt-BR') e o idioma do conteudo do diagnostico (DA-74).
    """
    policy = registered_tool_policy("diagnose_incident")
    request = incident_request(
        description=description,
        logs=logs,
        payload=payload,
        interface_type=interface_type,
        identifier=identifier,
        locale=locale,
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
    return connector_catalog()


def agent_interfaces() -> AgentInterfaces:
    tools = tuple(
        MCPTool(handler.__name__, handler, registered_tool_policy(handler.__name__))
        for handler in (diagnose_incident, list_connectors)
    )
    return AgentInterfaces(
        # Preserva tasks em Redis dos deploys IIC anteriores à migração.
        a2a=A2AConfig(AGENT_CARD, run_diagnosis, task_namespace=None),
        mcp=MCPConfig(
            name="sap-integration-copilot",
            version="0.1.0",
            tools=tools,
            granted_scopes=frozenset({"integration.read"}),
            instructions=(
                "Ferramentas de diagnostico de incidentes de integracao SAP/multi-vendor "
                "(OData/CPI, RFC/IDoc, ServiceNow, Salesforce, Workday, SAP Ariba, CAP, "
                "API Management). Todas as ferramentas sao read-only - nao criam, fecham "
                "nem alteram chamados em nenhum sistema externo."
            ),
        ),
    )
