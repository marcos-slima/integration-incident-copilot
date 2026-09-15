"""Modelos de estado do SAP Integration Copilot.

DiagnosisModel: schema de saida do LLM (validado via Pydantic).
CopilotState: estado compartilhado entre todos os nodes do grafo LangGraph.
"""

from typing import TypedDict

from pydantic import BaseModel, Field

from app.connectors import ConnectorResult


class DiagnosisModel(BaseModel):
    """Schema estruturado da resposta do LLM - usado via
    with_structured_output, valida o range de confidence na origem.

    IMPORTANTE: as descricoes (description=) nos campos abaixo NAO sao
    documentacao decorativa - o LangChain injeta esse texto no schema
    enviado ao LLM (via tool-calling), e e a UNICA orientacao semantica
    que o modelo recebe sobre o que cada campo significa. Removê-las
    (ou esquecer de adiciona-las) faz o LLM parar de saber o que
    preencher, mesmo continuando a raciocinar certo sobre o resto -
    foi exatamente isso que quebrou matched_source numa rodada anterior."""

    matched_source: str | None = Field(
        default=None,
        description=(
            "Nome EXATO do arquivo do documento de contexto usado como base "
            "para o diagnostico (ex: 'cpi_http_401.md'), copiado literalmente "
            "da linha 'fonte=...' do documento mais relevante fornecido. "
            "Use null se nenhum documento do contexto realmente corresponder "
            "ao incidente reportado."
        ),
    )
    probable_root_cause: str = Field(
        description=(
            "Causa raiz provavel do incidente, em uma ou duas frases, baseada "
            "EXCLUSIVAMENTE no documento de contexto fornecido e/ou nos dados "
            "reais do conector, quando disponiveis."
        )
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Numero entre 0.0 e 1.0 indicando o quanto o contexto disponivel "
            "sustenta essa causa raiz. Dados reais do conector aumentam a "
            "confianca; ausencia de correspondencia clara deve resultar em "
            "confianca baixa (abaixo de 0.4)."
        ),
    )
    next_steps: list[str] = Field(
        default_factory=list,
        description="Lista de proximos passos praticos e concretos para investigar ou resolver o incidente.",
    )


class CopilotState(TypedDict, total=False):
    description: str
    logs: str | None
    payload: str | None
    interface_type: str | None
    identifier: str | None
    llm_model: str
    connector_data: ConnectorResult | None
    retrieved_context: list[dict]
    graph_history: list
    diagnosis: dict
    web_search_results: list[dict]
    report_markdown: str
    debug: bool
