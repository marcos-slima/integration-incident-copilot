"""Agent Card do SAP Integration Copilot - protocolo A2A (Agent2Agent).

Ver docs/proposals/a2a-interoperability-layer.md para o contexto de
negocio completo (por que A2A, e a ressalva importante sobre a GA
inbound do Joule ainda nao ter chegado - Q4/2026 previsto).

Campos e formato do Agent Card verificados contra a especificacao A2A
vigente em 2026-09 (fonte: stacka2a.dev/blog/a2a-agent-card-json-schema,
consultado nesta sessao) - like a maioria dos protocolos de agente
"emergentes" citados na propria proposta original, o schema pode
evoluir; os campos obrigatorios (`name`, `description`, `version`,
`url`, `skills`) sao os mais estaveis e os unicos usados aqui alem dos
opcionais mais comuns (`capabilities`, `defaultInputModes`/
`defaultOutputModes`).

Publicado em `GET /.well-known/agent-card.json` (path padrao do
protocolo) por `app/main.py`.
"""

from app.config import settings

AGENT_CARD: dict = {
    "name": "SAP Integration Copilot",
    "description": (
        "Agente de diagnostico de incidentes de integracao - correlaciona "
        "dados de sistemas SAP (OData/CPI, RFC/IDoc) e nao-SAP (ServiceNow, "
        "Salesforce, Workday, SAP Ariba) com uma base de conhecimento via RAG "
        "para propor causa raiz provavel e proximos passos."
    ),
    "version": "0.1.0",
    "url": "/a2a",
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


def get_agent_card() -> dict:
    """Card completo. `a2a_api_key` NAO aparece aqui (nunca expor
    segredo no card publico) - so controla o header exigido nas
    chamadas ao endpoint JSON-RPC, ver app/a2a/server.py."""
    card = dict(AGENT_CARD)
    card["securitySchemes"] = (
        {
            "apiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "X-A2A-Api-Key",
            }
        }
        if settings.a2a_api_key
        else {}
    )
    card["security"] = [{"apiKeyAuth": []}] if settings.a2a_api_key else []
    return card
