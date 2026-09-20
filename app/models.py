"""Modelos Pydantic do SAP Integration Copilot."""

from typing import Literal

from pydantic import BaseModel, Field

# Limites generosos - rejeitam entrada absurda (ex: 1MB de log colado
# por engano) com 422 claro, sem impedir uso legitimo de logs longos.
MAX_DESCRIPTION_LENGTH = 5_000
MAX_LOGS_LENGTH = 50_000
MAX_PAYLOAD_LENGTH = 50_000


class IncidentRequest(BaseModel):
    description: str = Field(max_length=MAX_DESCRIPTION_LENGTH)
    logs: str | None = Field(default=None, max_length=MAX_LOGS_LENGTH)
    payload: str | None = Field(default=None, max_length=MAX_PAYLOAD_LENGTH)
    interface_type: (
        Literal["odata", "rfc", "servicenow", "salesforce", "workday", "ariba", "cap", "apim"]
        | None
    ) = None
    identifier: str | None = None  # ex: nome do iFlow, RFC destination, numero de IDoc/incidente


# DA-23 (Event Mesh): formato CloudEvents, o mesmo usado pelo SAP
# Event Mesh em modo REST/Webhook push subscription (alem de AMQP) -
# nao e um formato inventado por este projeto, e o envelope real que
# um assinante de webhook do Event Mesh recebe (type/source/id/time/
# data). Hoje so um `type` e reconhecido - qualquer outro e rejeitado
# com 422 automaticamente pelo Literal abaixo, em vez de tentar
# interpretar um payload de formato desconhecido silenciosamente.
INCIDENT_DETECTED_EVENT_TYPE = "com.sap.integration.incident.detected.v1"


class IncidentEventData(BaseModel):
    """Corpo (`data`) do evento - mesmos campos de IncidentRequest,
    pois o evento representa a MESMA informacao que um humano digitaria
    em /diagnose, so que originada automaticamente por um sistema de
    monitoracao (ex: CPI, Solution Manager, um listener de IDoc)."""

    description: str = Field(max_length=MAX_DESCRIPTION_LENGTH)
    logs: str | None = Field(default=None, max_length=MAX_LOGS_LENGTH)
    payload: str | None = Field(default=None, max_length=MAX_PAYLOAD_LENGTH)
    interface_type: (
        Literal["odata", "rfc", "servicenow", "salesforce", "workday", "ariba", "cap", "apim"]
        | None
    ) = None
    identifier: str | None = None


class IncidentEventEnvelope(BaseModel):
    """Envelope CloudEvents recebido em `POST /events/incident` - ver
    app/events/consumer.py para a conversao para IncidentRequest e o
    disparo automatico do diagnostico."""

    type: Literal["com.sap.integration.incident.detected.v1"]
    source: str | None = Field(
        default=None,
        description="Sistema de origem do evento (ex: 'cpi-monitor', 'solman'). Informativo.",
    )
    id: str | None = None
    time: str | None = None
    data: IncidentEventData


# DA-25 (Evidence/Trust Layer): cada Evidence aponta para uma fonte
# REAL que o pipeline de fato consultou (conector, RAG, GraphRAG, busca
# web ou a propria descricao do usuario) - a lista e montada de forma
# inteiramente deterministica em app/agent/nodes.py::_assemble_evidence,
# nunca a partir de autoavaliacao/citacao do LLM (mesmo principio ja
# usado em evidence_strength, DA-15). trust_level distingue "fato
# observado pelo sistema" de "hipotese/inferencia nao verificada" -
# sem isso, o consumidor da API nao tem como saber se uma causa raiz
# se apoia num dado real do conector ou so num resultado de busca web
# nao curado.
class Evidence(BaseModel):
    source_id: str = Field(
        description="Identificador legivel da fonte (ex: 'rag:cpi_http_401.md', 'connector:OData')."
    )
    source_type: Literal["connector", "rag", "graph", "web", "user"]
    locator: str | None = Field(
        default=None,
        description="Nome do documento/sistema referenciado, quando aplicavel (ex: nome do arquivo RAG).",
    )
    excerpt: str = Field(description="Trecho literal da fonte usado como evidencia (truncado).")
    retrieval_score: float | None = Field(
        default=None,
        description="Score de retrieval (cosseno denso) do RAG/GraphRAG, quando aplicavel.",
    )
    rerank_score: float | None = Field(
        default=None, description="Score do cross-encoder de reranking, quando aplicavel."
    )
    trust_level: Literal[
        "system_observed", "retrieved_document", "web_untrusted", "user_reported", "simulated"
    ] = Field(
        description=(
            "Nivel de confianca da fonte, decidido pelo TIPO da fonte "
            "(nunca por afirmacao do LLM): system_observed (dado real "
            "de conector) > retrieved_document (RAG/GraphRAG) > "
            "web_untrusted (busca web nao curada) > user_reported "
            "(descricao textual do usuario, nunca verificada) - "
            "'simulated' quando o conector retornou dado mock/fallback."
        )
    )


class DiagnosisResponse(BaseModel):
    probable_root_cause: str
    confidence: float = Field(ge=0.0, le=1.0)
    next_steps: list[str]
    report_markdown: str
    matched_source: str | None = None
    evidence_strength: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Sinal objetivo (nao auto-relatado pelo LLM) de quao "
            "fundamentado esta o diagnostico: dado real de conector ou "
            "score de retrieval do documento mais relevante. Ver DA-15."
        ),
    )
    llm_provider_used: str | None = Field(
        default=None,
        description=(
            "Provider LLM que efetivamente respondeu a este diagnostico "
            "('ollama', 'openai' ou 'azure_openai') - normalmente igual a "
            "settings.llm_provider, mas pode ser o provider de fallback "
            "(settings.llm_fallback_provider) se o primario estava "
            "indisponivel. Ver DA-20 (Hybrid Inference)."
        ),
    )
    agent_domain: str | None = Field(
        default=None,
        description=(
            "Dominio do sub-agente especialista que tratou o diagnostico "
            "('sap', 'saas' ou 'generic'), decidido deterministicamente "
            "pelo supervisor a partir de interface_type/descricao - nunca "
            "por autoavaliacao do LLM. Ver DA-22 (Multi-agent)."
        ),
    )
    evidence: list[Evidence] = Field(
        default_factory=list,
        description=(
            "Lista de evidencias que sustentam o diagnostico, uma por "
            "fonte real consultada (conector, RAG, GraphRAG, busca web, "
            "descricao do usuario) - montada deterministicamente pelo "
            "pipeline, nunca citada pelo LLM. Ver DA-25 (Evidence/Trust "
            "Layer)."
        ),
    )
    incident_id: str | None = Field(
        default=None,
        description=(
            "Id do incidente gravado no grafo de conhecimento (Neo4j), "
            "para referenciar depois em POST /incidents/{id}/verify "
            "(ver DA-28). None quando GraphRAG esta desligado ou o "
            "incidente nao tinha interface_type/identifier suficientes "
            "para ser gravado (upsert_incident_graph e no-op nesse "
            "caso)."
        ),
    )
    trace_id: str | None = Field(
        default=None,
        description=(
            "Id do trace Langfuse deste diagnostico, se o Langfuse "
            "estiver configurado e ativo - independente de GraphRAG "
            "(diferente de incident_id acima). Guarde este valor para "
            "enviar de volta em POST /incidents/{id}/verify (campo "
            "trace_id do corpo) e associar o feedback correto/incorreto "
            "ao trace certo no Langfuse. Avaliacao externa (medio "
            "prazo, item 5): 'Metricas e feedback'."
        ),
    )


class VerifyIncidentRequest(BaseModel):
    """DA-28 (VERIFIED_AS) + avaliacao externa (medio prazo, item 5 -
    'Metricas e feedback'): corpo de POST /incidents/{incident_id}/verify.

    Dois efeitos independentes, cada um so acontece se as
    pre-condicoes dele estiverem presentes - nenhum bloqueia o outro:
      1. Grava VERIFIED_AS no grafo (Neo4j) - exige GraphRAG ligado e
         `incident_id` correspondendo a um incidente ja gravado (ver
         app/rag/graph_store.py::verify_incident). Comportamento
         identico ao de antes desta mudanca (DA-28).
      2. Registra um score booleano ('correto'/'incorreto') no trace
         Langfuse do diagnostico original - exige `trace_id` (devolvido
         em DiagnosisResponse.trace_id) e Langfuse configurado.
    400 se NENHUM dos dois puder acontecer (GraphRAG desligado/
    incidente nao gravado E trace_id ausente) - nao ha nada credivel
    para fazer com a chamada nesse caso.
    """

    root_cause: str = Field(
        description="Causa raiz CONFIRMADA (pode diferir da hipotese original do LLM)."
    )
    verified_by: Literal["human", "system"] = Field(
        default="human",
        description="Quem verificou - 'human' (padrao) ou 'system' (ex: outra automacao confirmou).",
    )
    correct: bool | None = Field(
        default=None,
        description=(
            "Veredito do analista: o diagnostico original (hipotese do "
            "LLM) estava correto? None = nao informado (so grava "
            "root_cause no grafo, sem score de feedback no Langfuse, "
            "mesmo comportamento de antes desta mudanca). True/False "
            "gera um score booleano 'diagnosis_correct' no trace "
            "Langfuse referenciado por trace_id."
        ),
    )
    trace_id: str | None = Field(
        default=None,
        description=(
            "Id do trace Langfuse do diagnostico original "
            "(DiagnosisResponse.trace_id) - necessario para gravar o "
            "score de feedback no Langfuse. Sem isso, o feedback so "
            "afeta o grafo (Neo4j), quando GraphRAG estiver ligado."
        ),
    )
