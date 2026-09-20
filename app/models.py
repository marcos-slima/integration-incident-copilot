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
