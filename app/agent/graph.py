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
import re as re_module
from typing import TypedDict
from uuid import uuid4

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

from ddgs import DDGS
from langchain_core.tools import tool as lc_tool
from langfuse import get_client, observe
from langfuse.langchain import CallbackHandler
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from app.connectors import ConnectorResult, get_connector
from app.llm.factory import get_chat_model
from app.models import DiagnosisResponse, IncidentRequest
from app.rag.graph_store import (
    format_graph_context_for_prompt,
    graph_context,
    upsert_incident_graph,
)
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
    graph_history: list
    diagnosis: dict
    web_search_results: list[dict]
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


@observe(name="graph_enrich")
def graph_enrich_node(state: CopilotState) -> CopilotState:
    """So entra no grafo quando GRAPH_RAG_ENABLED=true (ver
    build_graph()) - consulta o Neo4j por incidentes anteriores na
    mesma interface, para enriquecer o prompt com recorrencia."""
    related = graph_context(state.get("interface_type"), state.get("identifier"))
    return {"graph_history": related}


@observe(name="graph_write")
def graph_write_node(state: CopilotState) -> CopilotState:
    """So entra no grafo quando GRAPH_RAG_ENABLED=true - grava o
    diagnostico concluido no Neo4j para alimentar consultas futuras de
    `graph_enrich_node`. No-op (via upsert_incident_graph) se nao houver
    interface/identificador associado a este incidente."""
    diagnosis = state.get("diagnosis", {})
    data = state.get("connector_data")
    upsert_incident_graph(
        incident_id=str(uuid4()),
        description=state["description"],
        interface_type=state.get("interface_type"),
        identifier=state.get("identifier"),
        source_system=data.source_system if data else None,
        root_cause=diagnosis.get("probable_root_cause", ""),
        confidence=float(diagnosis.get("confidence", 0.0)),
        matched_document=diagnosis.get("matched_source"),
    )
    return {}


@observe(name="web_search")
def web_search_node(state: CopilotState) -> CopilotState:
    """Busca web via DuckDuckGo - ativada apenas quando o RAG local
    nao encontrou contexto suficiente (todos os hits com score baixo
    ou nenhum hit). Direciona a busca para SAP Community e GitHub SAP
    para resultados mais relevantes ao contexto SAP/integracao.

    Privacidade: usa apenas a descricao textual do incidente, NUNCA
    dados do conector (que podem conter informacoes sensiveis do
    cliente como numeros de IDoc, nomes de sistema, etc.).

    So ativa quando confidence_threshold nao foi atingido pelo RAG
    local - nao substitui o RAG, e um fallback complementar."""
    hits = state.get("retrieved_context", [])
    top_score = hits[0]["score"] if hits else 0.0

    # Threshold: so busca na web se o melhor resultado RAG for fraco
    # Configuravel via WEB_SEARCH_THRESHOLD no .env (default: 0.6)
    if not settings.web_search_enabled or top_score >= settings.web_search_threshold:
        return {"web_search_results": []}

    description = state["description"]
    interface_type = state.get("interface_type", "")

    # Mapeamento de interface_type para fontes mais relevantes —
    # cada protocolo tem documentacao e comunidade especifica.
    # Fallback generico cobre casos sem interface_type definido.
    SITE_MAP = {
        "odata": "site:help.sap.com OR site:community.sap.com/t5/technology-blogs-by-sap",
        "rfc": "site:help.sap.com/docs/SAP_NETWEAVER OR site:community.sap.com OR site:github.com/SAP/PyRFC",
        "cap": "site:cap.cloud.sap OR site:github.com/SAP/cloud-cap-samples OR site:community.sap.com",
        "servicenow": "site:developer.servicenow.com OR site:community.sap.com OR site:help.sap.com",
        "salesforce": "site:developer.salesforce.com OR site:community.sap.com OR site:github.com/SAP",
        "workday": "site:community.workday.com OR site:community.sap.com",
        "ariba": "site:help.sap.com/docs/ARIBA OR site:community.sap.com",
        "apim": "site:help.sap.com/docs/SAP_API_MANAGEMENT OR site:community.sap.com",
    }
    site_filter = SITE_MAP.get(
        interface_type, "site:community.sap.com OR site:github.com/SAP OR site:help.sap.com"
    )
    tech_term = {
        "odata": "OData SAP Gateway",
        "rfc": "RFC ABAP BAPI",
        "cap": "SAP CAP CDS BTP",
        "servicenow": "ServiceNow SAP integration",
        "salesforce": "Salesforce SAP integration",
        "workday": "Workday SAP integration",
        "ariba": "SAP Ariba integration",
        "apim": "SAP API Management",
    }.get(interface_type, "SAP integration")

    query = f"{description} {tech_term} {site_filter}".strip()

    try:
        with DDGS() as ddgs:
            hits = list(ddgs.text(query, max_results=5))
        raw = "\n\n".join(
            f"Titulo: {h.get('title', '')}\nURL: {h.get('href', '')}\nResumo: {h.get('body', '')}"
            for h in hits
        )
        results = [{"source": "web_search", "text": raw, "score": 0.0}]
    except (OSError, ValueError, RuntimeError) as e:
        results = [{"source": "web_search_error", "text": str(e), "score": 0.0}]

    return {"web_search_results": results}


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

    graph_block = format_graph_context_for_prompt(state.get("graph_history", []))

    extras = ""
    if state.get("logs"):
        extras += f"\nLogs:\n{_truncate(state['logs'], MAX_LOGS_IN_PROMPT)}\n"
    if state.get("payload"):
        extras += f"\nPayload:\n{_truncate(state['payload'], MAX_PAYLOAD_IN_PROMPT)}\n"

    web_results = state.get("web_search_results", [])
    web_block = ""
    if web_results and web_results[0].get("source") == "web_search":
        web_block = f"""
Resultado de busca web (SAP Community / GitHub SAP) como contexto adicional:
{web_results[0]["text"][:2000]}
[Fonte: busca web - use como referencia secundaria, prefira o documento RAG acima se disponivel]
"""

    return f"""Voce e um especialista em integracao SAP (OData, IDoc, RFC, CPI).

Incidente reportado:
{state["description"]}
{extras}{connector_block}
Contexto recuperado da base de conhecimento de incidentes:
{context_block}
{others_note}{graph_block}{web_block}
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


def _make_web_search_tool(state):
    """Fabrica um tool de busca web contextualizado com o interface_type
    do incidente — o agente ReAct decide quando chamar."""
    interface_type = state.get("interface_type") or ""
    SITE_MAP = {
        "odata": "site:help.sap.com OR site:community.sap.com",
        "rfc": "site:help.sap.com/docs/SAP_NETWEAVER OR site:community.sap.com OR site:github.com/SAP",
        "cap": "site:cap.cloud.sap OR site:github.com/SAP/cloud-cap-samples OR site:community.sap.com",
        "servicenow": "site:developer.servicenow.com OR site:community.sap.com",
        "salesforce": "site:developer.salesforce.com OR site:community.sap.com",
        "workday": "site:community.workday.com OR site:community.sap.com",
        "ariba": "site:help.sap.com/docs/ARIBA OR site:community.sap.com",
        "apim": "site:help.sap.com/docs/SAP_API_MANAGEMENT OR site:community.sap.com",
    }
    site_filter = SITE_MAP.get(interface_type, "site:community.sap.com OR site:help.sap.com")

    @lc_tool
    def web_search_tool(query: str) -> str:
        """Busca informacao tecnica sobre incidente SAP em SAP Community e SAP Help.
        Use apenas termos tecnicos genericos — nunca dados sensiveis do cliente.

        Args:
            query: Termos tecnicos de busca (ex: 'BAPI_MATERIAL_SAVEDATA authorization error')
        """
        try:
            with DDGS() as ddgs:
                hits = list(ddgs.text(f"{query} {site_filter}", max_results=5))
            return "\n\n".join(
                f"Titulo: {h.get('title', '')}\nURL: {h.get('href', '')}\nResumo: {h.get('body', '')}"
                for h in hits
            )
        except (OSError, ValueError, RuntimeError) as e:
            return f"Erro na busca: {e}"

    return web_search_tool


@observe(name="diagnose")
def diagnose_node(state: CopilotState) -> CopilotState:
    model_name = state.get("llm_model") or settings.llm_model
    llm = get_chat_model(model_name)
    prompt = _build_diagnosis_prompt(state)

    if state.get("debug"):
        print("=" * 60)
        print("PROMPT ENVIADO AO LLM (ReAct v1.3):")
        print("=" * 60)
        print(prompt)
        print("=" * 60)

    # v1.3 - agente ReAct: o modelo decide autonomamente quando e
    # quantas vezes buscar na web antes de retornar o diagnostico.
    web_tool = _make_web_search_tool(state)
    react_agent = create_react_agent(llm, tools=[web_tool])
    # Instrucao adicional para forcar JSON na resposta final do agente ReAct
    json_instruction = """

Apos sua analise (usando o tool de busca se necessario), retorne OBRIGATORIAMENTE
um JSON valido com exatamente esta estrutura (sem texto adicional antes ou depois):
{
  "matched_source": "nome_do_arquivo.md ou null",
  "probable_root_cause": "causa raiz em uma ou duas frases",
  "confidence": 0.0,
  "next_steps": ["passo 1", "passo 2"]
}"""

    react_result = react_agent.invoke(
        {"messages": [{"role": "user", "content": prompt + json_instruction}]},
        config={"callbacks": [_langfuse_handler]},
    )

    last_msg = react_result["messages"][-1]
    raw = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

    if state.get("debug"):
        print("ITERACOES DO AGENTE ReAct:")
        for msg in react_result["messages"]:
            role = getattr(msg, "type", "msg")
            content = str(getattr(msg, "content", ""))
            if content:
                print(f"  [{role}] {content[:200]}")
        print("=" * 60)

    # Extrai JSON estruturado da resposta final do agente
    json_match = re_module.search(r'\{[^{}]*"probable_root_cause"[^{}]*\}', raw, re_module.DOTALL)
    if json_match:
        try:
            diagnosis = DiagnosisModel(**json.loads(json_match.group(0))).model_dump()
        except (json.JSONDecodeError, ValueError, KeyError):
            diagnosis = _fallback_diagnosis(raw)
    else:
        code_match = re_module.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re_module.DOTALL)
        if code_match:
            try:
                diagnosis = json.loads(code_match.group(1))
            except json.JSONDecodeError:
                diagnosis = _fallback_diagnosis(raw)
        else:
            diagnosis = _fallback_diagnosis(raw)

    diagnosis = _apply_confidence_guardrails(diagnosis, state)
    return {"diagnosis": diagnosis}


def _fallback_diagnosis(raw: str) -> dict:
    """Fallback quando o agente ReAct nao retornou JSON estruturado."""
    return {
        "probable_root_cause": raw[:500] if raw else "Nao foi possivel estruturar a resposta.",
        "confidence": 0.2,
        "matched_source": None,
        "next_steps": ["Verifique os logs do agente para mais detalhes."],
    }


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
    """O grafo tem duas formas: linear (default) ou com enriquecimento
    de GraphRAG intercalado, dependendo de `settings.graph_rag_enabled`
    - decidido uma vez na construcao, nao a cada execucao. Com
    GraphRAG desligado (default), o grafo e IDENTICO ao de antes desta
    fase - zero custo/comportamento novo. Ver app/rag/graph_store.py
    para como ativar de verdade."""
    graph = StateGraph(CopilotState)
    graph.add_node("connector", connector_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("web_search", web_search_node)
    graph.add_node("diagnose", diagnose_node)
    graph.add_node("report", report_node)

    graph.set_entry_point("connector")
    graph.add_edge("connector", "retrieve")
    graph.add_edge("retrieve", "web_search")

    if settings.graph_rag_enabled:
        graph.add_node("graph_enrich", graph_enrich_node)
        graph.add_node("graph_write", graph_write_node)
        graph.add_edge("web_search", "graph_enrich")
        graph.add_edge("graph_enrich", "diagnose")
        graph.add_edge("diagnose", "graph_write")
        graph.add_edge("graph_write", "report")
    else:
        graph.add_edge("web_search", "diagnose")
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
    parser.add_argument(
        "--interface",
        choices=["odata", "rfc", "servicenow", "salesforce", "workday", "ariba"],
        default=None,
    )
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
