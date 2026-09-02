"""Grafo LangGraph do SAP Integration Copilot.

Fluxo linear:

    connector -> retrieve -> diagnose -> report

Instrumentado com Langfuse: cada node vira um span (@observe), e a
chamada ao LLM e rastreada via CallbackHandler do LangChain.

Correcoes aplicadas apos code review:
  - modelo do LLM injetado via state/parametro, sem global mutavel
  - saida do LLM estruturada via Pydantic (with_structured_output),
    com fallback pro parsing manual se a validacao estruturada falhar
  - confidence validado por Field(ge=0,le=1) + clamp defensivo no codigo
  - logs/payload truncados antes de entrar no prompt (protege contexto)

O node `diagnose` obtem o chat model via `app.llm.factory.get_chat_model()`
(o "LLM Gateway"), nao instancia `ChatOllama` diretamente - o provedor
(Ollama local, OpenAI, Azure OpenAI) vem de `Settings.llm_provider`,
sem precisar tocar neste arquivo (ver Decisao de Arquitetura #10 no
README).

Uso:
    from app.agent.graph import run_diagnosis
    from app.models import IncidentRequest

    result = run_diagnosis(IncidentRequest(description="..."))
"""

import json
import os
from typing import TypedDict

from app.config import settings

# Bridge das credenciais do .env para as variaveis de ambiente que o
# SDK do Langfuse espera - isso e o padrao de configuracao esperado
# por aquele SDK especifico (le de env var por design), diferente do
# problema de "global mutavel" do item 1 abaixo (que era estado
# mutado em RUNTIME por chamadas subsequentes, nao configuracao lida
# uma vez no import).
os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)
os.environ.setdefault("LANGFUSE_BASE_URL", settings.langfuse_host)

from langfuse import get_client, observe
from langfuse.langchain import CallbackHandler
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from app.connectors import ConnectorResult, get_connector
from app.llm.factory import get_chat_model
from app.models import DiagnosisResponse, IncidentRequest
from app.rag.retriever import retrieve

_langfuse_handler = CallbackHandler()

# Limites praticos de contexto enviado ao LLM - bem mais apertados que
# o max_length do Pydantic (que so protege a API de payload absurdo).
MAX_LOGS_IN_PROMPT = 2_000
MAX_PAYLOAD_IN_PROMPT = 2_000


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
    diagnosis: dict
    report_markdown: str
    debug: bool


@observe(name="connector")
def connector_node(state: CopilotState) -> CopilotState:
    interface_type = state.get("interface_type")
    if not interface_type:
        return {"connector_data": None}

    connector = get_connector(interface_type)
    result = connector.fetch(state.get("identifier") or "")
    return {"connector_data": result}


def _effective_query(state: CopilotState) -> str:
    base = state["description"]
    data = state.get("connector_data")
    if data:
        return f"{base}\n{data.message}"
    return base


@observe(name="retrieve")
def retrieve_node(state: CopilotState) -> CopilotState:
    hits = retrieve(_effective_query(state), target="incidents", top_k=3)
    return {"retrieved_context": hits}


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[...truncado - {len(text) - limit} caracteres omitidos...]"


def _build_diagnosis_prompt(state: CopilotState) -> str:
    hits = state.get("retrieved_context", [])
    top_hit = hits[0] if hits else None
    other_sources = [h["source"] for h in hits[1:]]

    if top_hit:
        context_block = (
            f"--- Documento mais relevante (fonte={top_hit['source']}, "
            f"score={top_hit['score']:.3f}) ---\n{top_hit['text']}"
        )
    else:
        context_block = "(nenhum contexto relevante encontrado)"

    others_note = (
        f"\nOutras fontes candidatas, menos relevantes, cujo conteudo NAO foi "
        f"incluido aqui (ignore-as a menos que o documento acima claramente nao "
        f"corresponda ao incidente): {', '.join(other_sources)}\n"
        if other_sources
        else ""
    )

    connector_block = ""
    data = state.get("connector_data")
    if data:
        fallback_warning = (
            "\n  ATENCAO: este e um dado GENERICO DE FALLBACK - o identificador "
            "informado nao foi reconhecido pelo sistema. NAO trate isso como um "
            "erro especifico conhecido. A menos que a descricao textual do "
            "incidente, por si so, bata claramente com o documento de contexto, "
            "use confidence baixa (< 0.4) e considere matched_source como null."
            if data.is_fallback
            else ""
        )
        connector_block = f"""
Dados coletados diretamente do sistema SAP (via conector {data.source_system}{" - SIMULADO/MOCK" if data.is_mock else ""}):
  status: {data.status}
  codigo de erro: {data.error_code}
  mensagem: {data.message}
  detalhe bruto: {data.raw}{fallback_warning}
"""

    extras = ""
    if state.get("logs"):
        extras += f"\nLogs:\n{_truncate(state['logs'], MAX_LOGS_IN_PROMPT)}\n"
    if state.get("payload"):
        extras += f"\nPayload:\n{_truncate(state['payload'], MAX_PAYLOAD_IN_PROMPT)}\n"

    return f"""Voce e um especialista em integracao SAP (OData, IDoc, RFC, CPI).

Incidente reportado:
{state["description"]}
{extras}{connector_block}
Contexto recuperado da base de conhecimento de incidentes:
{context_block}
{others_note}
Regra importante: baseie sua resposta EXCLUSIVAMENTE no documento de
contexto acima e, se disponivel, nos dados reais do conector (que tem
prioridade sobre a descricao textual do usuario, pois vem diretamente
do sistema). Nao combine informacoes de outros documentos. Se o
documento acima nao corresponder ao sintoma descrito, diga isso e use
confidence baixa em vez de inventar uma causa raiz combinando temas
diferentes.

No campo matched_source, copie EXATAMENTE o nome do arquivo indicado
apos "fonte=" no cabecalho do documento mais relevante mostrado acima
(exemplo: se o cabecalho diz "fonte=cpi_http_401.md", o valor de
matched_source deve ser exatamente "cpi_http_401.md", sem alteracoes).
Se nenhum documento corresponder ao incidente, use null nesse campo.

"confidence" deve ser um numero entre 0.0 e 1.0. Se houver dados reais
do conector confirmando o diagnostico, a confidence pode ser mais alta
(o dado do sistema e mais confiavel que so a descricao textual)."""


def _apply_confidence_guardrails(diagnosis: dict, state: CopilotState) -> dict:
    """Guardrails deterministicos - nao confia so na autoavaliacao do
    LLM nem so na validacao de schema."""
    diagnosis["confidence"] = max(0.0, min(1.0, float(diagnosis.get("confidence", 0.0))))

    data = state.get("connector_data")
    if data and data.is_fallback:
        original = diagnosis["confidence"]
        capped = min(original, 0.4)
        if capped < original:
            diagnosis["confidence"] = capped
            diagnosis["probable_root_cause"] = (
                f"[confianca limitada - identificador nao reconhecido pelo sistema] "
                f"{diagnosis.get('probable_root_cause', '')}"
            )

    if not state.get("retrieved_context") and not data:
        original = diagnosis["confidence"]
        capped = min(original, 0.3)
        if capped < original:
            diagnosis["confidence"] = capped
            diagnosis["matched_source"] = None
            diagnosis["probable_root_cause"] = (
                f"[confianca limitada - nenhum documento relevante encontrado] "
                f"{diagnosis.get('probable_root_cause', '')}"
            )

    return diagnosis


@observe(name="diagnose")
def diagnose_node(state: CopilotState) -> CopilotState:
    model_name = state.get("llm_model") or settings.llm_model
    llm = get_chat_model(model_name)
    prompt = _build_diagnosis_prompt(state)

    structured_llm = llm.with_structured_output(DiagnosisModel, include_raw=True)
    result = structured_llm.invoke(prompt, config={"callbacks": [_langfuse_handler]})
    raw_message = result["raw"]
    parsed: DiagnosisModel | None = result["parsed"]

    if state.get("debug"):
        print("=" * 60)
        print("PROMPT ENVIADO AO LLM:")
        print("=" * 60)
        print(prompt)
        print("=" * 60)
        print("RESPOSTA BRUTA DO LLM:")
        print("=" * 60)
        print(raw_message.content if raw_message else "(sem conteudo bruto)")
        print("=" * 60)

    if parsed is not None:
        diagnosis = parsed.model_dump()
    else:
        raw = (raw_message.content or "").strip() if raw_message else ""
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw.removeprefix("json")
            raw = raw.strip()
        try:
            diagnosis = json.loads(raw)
        except json.JSONDecodeError:
            diagnosis = {
                "probable_root_cause": "Nao foi possivel estruturar a resposta do modelo.",
                "confidence": 0.0,
                "next_steps": [f"Resposta bruta do modelo: {raw[:500]}"],
            }

    diagnosis = _apply_confidence_guardrails(diagnosis, state)
    return {"diagnosis": diagnosis}


@observe(name="report")
def report_node(state: CopilotState) -> CopilotState:
    diagnosis = state.get("diagnosis", {})
    sources = ", ".join(sorted({h["source"] for h in state.get("retrieved_context", [])})) or (
        "nenhuma fonte relevante encontrada"
    )

    next_steps_md = "\n".join(f"- {step}" for step in diagnosis.get("next_steps", []))
    matched = diagnosis.get("matched_source") or "nenhum documento especifico identificado"

    connector_line = ""
    data = state.get("connector_data")
    if data:
        connector_line = (
            f"\n**Dados do sistema ({data.source_system}"
            f"{' - simulado' if data.is_mock else ''}):** "
            f"status={data.status}, codigo={data.error_code}\n"
        )

    report = f"""## Diagnostico do Incidente

**Descricao reportada:** {state["description"]}
{connector_line}
**Causa raiz provavel:** {diagnosis.get("probable_root_cause", "N/A")}

**Confianca:** {diagnosis.get("confidence", 0.0):.0%}

**Documento usado como base:** {matched}

**Proximos passos:**
{next_steps_md if next_steps_md else "- (nenhum passo sugerido)"}

**Fontes recuperadas (candidatas):** {sources}
"""
    return {"report_markdown": report}


def build_graph():
    graph = StateGraph(CopilotState)
    graph.add_node("connector", connector_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("diagnose", diagnose_node)
    graph.add_node("report", report_node)

    graph.set_entry_point("connector")
    graph.add_edge("connector", "retrieve")
    graph.add_edge("retrieve", "diagnose")
    graph.add_edge("diagnose", "report")
    graph.add_edge("report", END)

    return graph.compile()


_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


@observe(name="sap_copilot_diagnosis")
def run_diagnosis(
    request: IncidentRequest,
    debug: bool = False,
    llm_model: str | None = None,
) -> DiagnosisResponse:
    """Executa o grafo completo para um incidente.

    llm_model: override opcional do modelo (ex: usado pelo
    promptfoo_provider.py para comparacao de modelos) - passado via
    parametro/state, nao mais via mutacao de global de modulo.
    """
    initial_state: CopilotState = {
        "description": request.description,
        "logs": request.logs,
        "payload": request.payload,
        "interface_type": request.interface_type,
        "identifier": request.identifier,
        "llm_model": llm_model or settings.llm_model,
        "debug": debug,
    }
    final_state = get_graph().invoke(initial_state)
    diagnosis = final_state.get("diagnosis", {})

    return DiagnosisResponse(
        probable_root_cause=diagnosis.get("probable_root_cause", "N/A"),
        confidence=float(diagnosis.get("confidence", 0.0)),
        next_steps=diagnosis.get("next_steps", []),
        report_markdown=final_state.get("report_markdown", ""),
        matched_source=diagnosis.get("matched_source"),
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("description", nargs="*", default=[])
    parser.add_argument("--interface", choices=["odata", "rfc", "servicenow"], default=None)
    parser.add_argument("--id", dest="identifier", default=None)
    parser.add_argument("--model", dest="llm_model", default=None, help="Override do modelo LLM")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    description = (
        " ".join(args.description) or "iFlow falhando com HTTP 401 ao chamar endpoint externo"
    )
    request = IncidentRequest(
        description=description,
        interface_type=args.interface,
        identifier=args.identifier,
    )
    result = run_diagnosis(request, debug=args.debug, llm_model=args.llm_model)
    print(result.report_markdown)

    get_client().flush()
