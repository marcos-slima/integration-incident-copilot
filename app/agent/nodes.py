"""Nodes do grafo LangGraph do SAP Integration Copilot.

Cada node e uma funcao pura que recebe e retorna CopilotState.
Instrumentado com Langfuse via @observe.
"""

import json
import logging
import os
import re as re_module
from uuid import uuid4

from app.config import settings

os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)
os.environ.setdefault("LANGFUSE_BASE_URL", settings.langfuse_host)

from ddgs import DDGS
from langchain_core.tools import tool as lc_tool
from langfuse import get_client, observe
from langfuse.langchain import CallbackHandler
from langgraph.prebuilt import create_react_agent

from app.agent.state import CopilotState, DiagnosisModel
from app.connectors import get_connector
from app.llm.factory import get_chat_model
from app.rag.graph_store import (
    format_graph_context_for_prompt,
    graph_context,
    upsert_incident_graph,
)
from app.rag.retriever import retrieve

_langfuse_handler = CallbackHandler()

MAX_LOGS_IN_PROMPT = 3_000
MAX_PAYLOAD_IN_PROMPT = 3_000


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


def _record_quality_metrics(state: CopilotState, diagnosis: dict) -> None:
    """Grava metricas de qualidade no trace Langfuse atual.

    Metricas gravadas:
    - confidence: confianca do diagnostico (0-1)
    - has_matched_source: 1 se encontrou documento, 0 se nao (proxy de hallucination)
    - rerank_top_score: score do reranker no top resultado (qualidade do retrieval)
    - web_search_used: 1 se a busca web foi ativada nesta execucao
    """
    try:
        client = get_client()
        confidence = float(diagnosis.get("confidence", 0.0))
        has_source = 1.0 if diagnosis.get("matched_source") else 0.0
        web_used = (
            1.0
            if state.get("web_search_results")
            and state["web_search_results"]
            and state["web_search_results"][0].get("source") == "web_search"
            else 0.0
        )

        top_hit = state.get("retrieved_context", [{}])[0] if state.get("retrieved_context") else {}
        rerank_score = float(top_hit.get("rerank_score", top_hit.get("score", 0.0)))

        client.score_current_trace(name="confidence", value=confidence)
        client.score_current_trace(name="has_matched_source", value=has_source)
        client.score_current_trace(name="rerank_top_score", value=rerank_score)
        client.score_current_trace(name="web_search_used", value=web_used)
    except Exception:
        logging.getLogger(__name__).debug("Langfuse metrics error", exc_info=True)


@observe(name="report")
def report_node(state: CopilotState) -> CopilotState:
    diagnosis = state.get("diagnosis", {})
    _record_quality_metrics(state, diagnosis)
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
