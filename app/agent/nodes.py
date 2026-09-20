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
from langfuse import Langfuse, get_client, observe
from langfuse.langchain import CallbackHandler
from langgraph.prebuilt import create_react_agent

from app.agent.state import CopilotState, DiagnosisModel
from app.connectors import get_connector
from app.llm.factory import TRANSPORT_FAILURE_EXCEPTIONS
from app.llm.gateway import invoke_via_gateway
from app.rag.graph_store import (
    GRAPH_UNAVAILABLE_EXCEPTIONS,
    format_graph_context_for_prompt,
    graph_context,
    upsert_incident_graph,
)
from app.rag.retriever import retrieve
from app.redaction import redact_pii_deep, redact_pii_text

# Avaliacao externa (medio prazo, item 4): inicializa o client Langfuse
# EXPLICITAMENTE com mask=redact_pii_deep, ANTES de qualquer
# CallbackHandler()/@observe rodar - "get_client()" so cria o client
# default (sem mask) se nenhum ja existir, entao a ordem aqui importa.
# Isso cobre "antes do Langfuse": o SDK aplica essa mascara a QUALQUER
# input/output que @observe capturar automaticamente (o CopilotState
# inteiro, nao so o texto que sanitize_untrusted_input ja sanitizava
# manualmente para o prompt).
Langfuse(mask=redact_pii_deep)
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
    mesma interface, para enriquecer o prompt com recorrencia.

    DA-21: uma falha de infraestrutura do Neo4j (conexao recusada,
    timeout, servidor temporariamente indisponivel) nao pode derrubar
    o diagnostico inteiro - degrada graciosamente para "sem historico"
    e loga um warning. Erros que indicam bug nosso (Cypher invalido,
    violacao de constraint) continuam propagando normalmente."""
    try:
        related = graph_context(state.get("interface_type"), state.get("identifier"))
    except GRAPH_UNAVAILABLE_EXCEPTIONS as exc:
        logging.getLogger(__name__).warning(
            "graph_enrich_node: Neo4j indisponivel, seguindo sem historico do grafo: %s",
            exc,
        )
        return {"graph_history": []}
    return {"graph_history": related}


@observe(name="graph_write")
def graph_write_node(state: CopilotState) -> CopilotState:
    """So entra no grafo quando GRAPH_RAG_ENABLED=true - grava o
    diagnostico concluido no Neo4j para alimentar consultas futuras de
    `graph_enrich_node`. No-op (via upsert_incident_graph) se nao houver
    interface/identificador associado a este incidente.

    DA-21: mesma logica de degradacao graciosa de graph_enrich_node -
    se o Neo4j estiver temporariamente fora do ar, o diagnostico ja
    concluido nao pode ser perdido/travado so porque a escrita de
    conhecimento operacional falhou. Loga um warning e segue."""
    diagnosis = state.get("diagnosis", {})
    data = state.get("connector_data")
    try:
        upsert_incident_graph(
            # DA-28: usa o id gerado em graph.py::run_diagnosis (agora
            # tambem devolvido em DiagnosisResponse.incident_id), em
            # vez de gerar um novo aqui - sem isso o id gravado no
            # Neo4j nunca chegava ao caller, e nao havia como chamar
            # verify_incident() depois. Fallback pra uuid4() so por
            # seguranca (chamada direta a graph_write_node sem passar
            # por run_diagnosis, ex: um teste antigo).
            incident_id=state.get("incident_id") or str(uuid4()),
            description=state["description"],
            interface_type=state.get("interface_type"),
            identifier=state.get("identifier"),
            source_system=data.source_system if data else None,
            root_cause=diagnosis.get("probable_root_cause", ""),
            confidence=float(diagnosis.get("confidence", 0.0)),
            matched_document=diagnosis.get("matched_source"),
            evidence_strength=float(diagnosis.get("evidence_strength", 0.0)),
        )
    except GRAPH_UNAVAILABLE_EXCEPTIONS as exc:
        logging.getLogger(__name__).warning(
            "graph_write_node: Neo4j indisponivel, diagnostico concluido mas nao "
            "gravado no grafo de conhecimento: %s",
            exc,
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


# Padroes de prompt injection mais comuns em contexto SAP/LLM
# Sem "(?i)" por padrao: a partir do Python 3.11, uma flag inline só é
# valida no INICIO da expressao inteira - repeti-la em cada padrao
# individual (como estava antes) quebra o re.compile("|".join(...))
# com "global flags not at the start of the expression" assim que mais
# de um padrao com (?i) e unido. Case-insensitive agora e aplicado uma
# unica vez via re.IGNORECASE no re.compile (ver _get_injection_re).
_INJECTION_PATTERNS = [
    # Instrucoes diretas ao modelo
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?",
    r"disregard\s+(all\s+)?(previous|prior|above)\s+instructions?",
    r"forget\s+(all\s+)?(previous|prior|above)\s+instructions?",
    r"you\s+are\s+now\s+a",
    r"act\s+as\s+(a\s+)?(?:different|new|another)",
    r"new\s+instructions?:",
    r"system\s*:\s*you",
    r"\[system\]",
    r"\<\s*system\s*\>",
    # Exfiltracao de dados
    r"print\s+(all\s+)?(your\s+)?(system\s+)?prompt",
    r"reveal\s+(your\s+)?(system\s+)?prompt",
    r"show\s+(me\s+)?(your\s+)?(instructions?|prompt|context)",
    # Jailbreak comum
    r"DAN\s+mode",
    r"developer\s+mode",
    r"jailbreak",
]

_INJECTION_RE = None


def _get_injection_re():
    global _INJECTION_RE
    if _INJECTION_RE is None:
        import re

        _INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)
    return _INJECTION_RE


def sanitize_untrusted_input(text: str | None, field_name: str = "input") -> str:
    """Sanitiza entrada nao confiavel antes de incluir no prompt LLM.

    Remove ou neutraliza padroes de prompt injection conhecidos.
    Nao e uma protecao completa — defense in depth, nao silver bullet.
    Campos sanitizados: description, logs, payload, connector_data,
    chunks do RAG (que podem vir de PDFs externos) e resultados de
    busca web (paginas de terceiros, mesmo grau de confianca que um
    PDF externo). Avaliacao externa (nova revisao, P1): antes desta
    correcao, a docstring ja afirmava isso, mas description e o
    resultado de busca web eram interpolados CRUS em
    _build_diagnosis_prompt() - o unico campo de fato nao confiavel
    (digitado livremente pelo usuario) que chegava ao LLM sem passar
    por aqui era justamente o mais obvio.

    Args:
        text: Texto a sanitizar
        field_name: Nome do campo (para logging)

    Returns:
        Texto sanitizado, ou string vazia se None
    """
    if not text:
        return ""

    original_len = len(text)
    pattern = _get_injection_re()

    # Substitui padroes de injection por marcador explicito
    sanitized = pattern.sub("[CONTEUDO_REMOVIDO_INJECTION]", text)

    if len(sanitized) != original_len:
        import logging

        logging.getLogger(__name__).warning(
            "Possivel prompt injection detectado no campo '%s' — conteudo neutralizado",
            field_name,
        )

    # Avaliacao externa (medio prazo, item 4): redaction de PII "antes
    # do prompt" - e-mail/CPF/numero de IDoc nunca chegam ao LLM neste
    # campo (ver app/redaction.py). Depois da sanitizacao de injection
    # (ordem nao importa para correcao, mas mantem os dois tipos de
    # neutralizacao juntos, no mesmo lugar onde este campo ja era
    # tratado como nao-confiavel).
    return redact_pii_text(sanitized)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[...truncado - {len(text) - limit} caracteres omitidos...]"


def _build_diagnosis_prompt(state: CopilotState, persona: str) -> str:
    hits = state.get("retrieved_context", [])
    top_hit = hits[0] if hits else None
    other_sources = [h["source"] for h in hits[1:]]

    if top_hit:
        safe_chunk_text = sanitize_untrusted_input(top_hit["text"], "rag_chunk")
        context_block = (
            f"--- Documento mais relevante (fonte={top_hit['source']}, "
            f"score={top_hit['score']:.3f}) ---\n{safe_chunk_text}"
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
        safe_error_code = sanitize_untrusted_input(
            str(data.error_code) if data.error_code else "", "connector_error_code"
        )
        safe_message = sanitize_untrusted_input(data.message, "connector_message")
        safe_raw = sanitize_untrusted_input(str(data.raw) if data.raw else "", "connector_raw")
        connector_block = f"""
Dados coletados diretamente do sistema SAP (via conector {data.source_system}{" - SIMULADO/MOCK" if data.is_mock else ""}):
  status: {data.status}
  codigo de erro: {safe_error_code}
  mensagem: {safe_message}
  detalhe bruto: {safe_raw}{fallback_warning}
"""

    graph_block = format_graph_context_for_prompt(state.get("graph_history", []))

    extras = ""
    if state.get("logs"):
        safe_logs = sanitize_untrusted_input(state["logs"], "logs")
        extras += f"\nLogs:\n{_truncate(safe_logs, MAX_LOGS_IN_PROMPT)}\n"
    if state.get("payload"):
        safe_payload = sanitize_untrusted_input(state["payload"], "payload")
        extras += f"\nPayload:\n{_truncate(safe_payload, MAX_PAYLOAD_IN_PROMPT)}\n"

    web_results = state.get("web_search_results", [])
    web_block = ""
    if web_results and web_results[0].get("source") == "web_search":
        safe_web_text = sanitize_untrusted_input(web_results[0]["text"][:2000], "web_search_result")
        web_block = f"""
Resultado de busca web (SAP Community / GitHub SAP) como contexto adicional:
{safe_web_text}
[Fonte: busca web - use como referencia secundaria, prefira o documento RAG acima se disponivel]
"""

    safe_description = sanitize_untrusted_input(state["description"], "description")

    return f"""{persona}

Incidente reportado:
{safe_description}
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


# Margem de tolerancia entre confidence auto-relatada pelo LLM e a
# evidence_strength calculada a partir do retrieval/conector reais.
# DA-15: sem isso, o modelo pode "soar confiante" (0.8, 0.9) mesmo
# quando o retrieval sustenta pouco (score baixo) - a autoavaliacao do
# LLM nao e uma metrica de evidencia, e o teto anterior (0.4/0.3 fixos,
# so em 2 cenarios binarios) nao cobria o espectro continuo de scores.
EVIDENCE_CONFIDENCE_MARGIN = 0.25


def _compute_evidence_strength(state: CopilotState) -> float:
    """Evidence_strength: sinal objetivo (nao auto-relatado pelo LLM) de
    quao bem fundamentado esta o contexto disponivel para o diagnostico.

    Fontes, em ordem de forca:
    - Dado real de conector (nao mock, nao fallback) e o sinal mais forte
      -> piso de 0.75, pois vem diretamente do sistema, nao de inferencia.
    - Score de retrieval (rerank_score se disponivel, senao score bruto
      do hybrid RAG) do documento mais relevante.
    - Nenhum contexto (sem RAG, sem conector real) -> 0.0.
    """
    data = state.get("connector_data")
    hits = state.get("retrieved_context") or []
    top_hit = hits[0] if hits else {}
    rag_score = float(top_hit.get("rerank_score", top_hit.get("score", 0.0))) if top_hit else 0.0

    connector_is_real_evidence = bool(data) and not data.is_mock and not data.is_fallback
    strength = max(rag_score, 0.75) if connector_is_real_evidence else rag_score
    return max(0.0, min(1.0, strength))


def _assemble_evidence(state: CopilotState) -> list[dict]:
    """DA-25 (Evidence/Trust Layer): monta a lista de evidencias que
    sustentam o diagnostico de forma inteiramente DETERMINISTICA -
    nunca a partir de autoavaliacao/citacao do LLM (mesmo principio ja
    usado em _compute_evidence_strength/DA-15: guardrails em codigo,
    nao em prompt - a autoavaliacao do LLM nao e uma metrica confiavel,
    e o mesmo vale para "quais fontes o LLM diz ter usado"). Cada item
    aponta para uma fonte REAL que o pipeline de fato consultou nesta
    execucao, com trust_level decidido pelo TIPO da fonte:

    - system_observed: dado real de conector (nao mock, nao fallback)
    - simulated: dado de conector mock/fallback (nao um sistema real)
    - retrieved_document: chunk RAG (Qdrant, ja passado pelo reranker)
      ou historico do GraphRAG
    - web_untrusted: resultado de busca web (DuckDuckGo), nao curado
    - user_reported: a propria descricao textual do incidente - nunca
      verificada de forma independente, e o sinal mais fraco de todos.
    """
    evidence: list[dict] = []

    data = state.get("connector_data")
    if data is not None:
        evidence.append(
            {
                "source_id": f"connector:{data.source_system}",
                "source_type": "connector",
                "locator": data.source_system,
                "excerpt": _truncate(data.message or "", 500),
                "retrieval_score": None,
                "rerank_score": None,
                "trust_level": "simulated"
                if (data.is_mock or data.is_fallback)
                else "system_observed",
            }
        )

    for hit in state.get("retrieved_context") or []:
        evidence.append(
            {
                "source_id": f"rag:{hit.get('source')}",
                "source_type": "rag",
                "locator": hit.get("source"),
                "excerpt": _truncate(hit.get("text") or "", 500),
                "retrieval_score": hit.get("score"),
                "rerank_score": hit.get("rerank_score"),
                "trust_level": "retrieved_document",
            }
        )

    for related in state.get("graph_history") or []:
        evidence.append(
            {
                "source_id": f"graph:{related.source_system}:{related.matched_document or 'sem-documento'}",
                "source_type": "graph",
                "locator": related.matched_document,
                "excerpt": _truncate(related.root_cause or "", 500),
                "retrieval_score": related.evidence_strength,
                "rerank_score": None,
                "trust_level": "retrieved_document",
            }
        )

    web_results = state.get("web_search_results") or []
    if web_results and web_results[0].get("source") == "web_search":
        evidence.append(
            {
                "source_id": "web:duckduckgo",
                "source_type": "web",
                "locator": None,
                "excerpt": _truncate(web_results[0].get("text") or "", 500),
                "retrieval_score": None,
                "rerank_score": None,
                "trust_level": "web_untrusted",
            }
        )

    description = state.get("description")
    if description:
        evidence.append(
            {
                "source_id": "user:description",
                "source_type": "user",
                "locator": None,
                "excerpt": _truncate(description, 500),
                "retrieval_score": None,
                "rerank_score": None,
                "trust_level": "user_reported",
            }
        )

    return evidence


def _apply_confidence_guardrails(diagnosis: dict, state: CopilotState) -> dict:
    """Guardrails deterministicos - nao confia so na autoavaliacao do
    LLM nem so na validacao de schema."""
    diagnosis["confidence"] = max(0.0, min(1.0, float(diagnosis.get("confidence", 0.0))))

    evidence_strength = _compute_evidence_strength(state)
    diagnosis["evidence_strength"] = round(evidence_strength, 3)

    evidence_ceiling = min(1.0, evidence_strength + EVIDENCE_CONFIDENCE_MARGIN)
    diagnosis["confidence"] = min(diagnosis["confidence"], evidence_ceiling)

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


# DA-22 (Multi-agent): antes desta fase existia UM diagnose_node com
# persona fixa de "especialista SAP", usado para qualquer conector -
# incoerente com o principio de design multi-fornecedor do projeto
# (SAP e um conector entre iguais). Agora `_run_diagnosis_agent` e o
# nucleo compartilhado (ReAct + hybrid inference + parsing + guardrails,
# tudo igual a antes) parametrizado por `persona`, e dois sub-agentes
# especialistas (`sap_diagnosis_node`, `saas_diagnosis_node`) o chamam
# com personas diferentes. O supervisor (app/agent/supervisor.py) decide
# QUAL dos dois roda, via roteamento condicional em app/agent/graph.py -
# nunca os dois no mesmo incidente (custo de LLM nao duplica).
_SAP_SPECIALIST_PERSONA = (
    "Voce e um especialista em integracao SAP (OData, IDoc, RFC, CPI/Integration Suite, BTP)."
)
_ENTERPRISE_SPECIALIST_PERSONA = (
    "Voce e um especialista em integracoes empresariais multi-fornecedor "
    "(ServiceNow, Salesforce, Workday, Ariba e APIs corporativas em geral) - "
    "conhece padroes tipicos de falha em REST/OAuth2, webhooks, rate limits "
    "e sincronizacao de dados entre sistemas terceiros. Quando o fornecedor "
    "especifico do incidente nao estiver identificado, aplique o mesmo "
    "raciocinio generalista de troubleshooting de integracao de sistemas."
)


def _run_diagnosis_agent(state: CopilotState, persona: str) -> dict:
    model_name = state.get("llm_model") or settings.llm_model
    prompt = _build_diagnosis_prompt(state, persona)

    if state.get("debug"):
        print("=" * 60)
        print("PROMPT ENVIADO AO LLM (ReAct v1.3):")
        print("=" * 60)
        print(prompt)
        print("=" * 60)

    # v1.3 - agente ReAct: o modelo decide autonomamente quando e
    # quantas vezes buscar na web antes de retornar o diagnostico.
    web_tool = _make_web_search_tool(state)
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

    def _build_and_invoke(llm):
        # Avaliacao externa (curto prazo, item 5): "structured output de
        # verdade" - response_format=DiagnosisModel faz o
        # create_react_agent (langgraph>=1.x) rodar uma chamada ADICIONAL
        # ao LLM apos o loop ReAct terminar, usando with_structured_output
        # de verdade (tool-calling nativo do provider), e devolve o
        # resultado ja validado em react_result["structured_response"] -
        # nao mais so um texto que a gente torce pra estar em JSON. O
        # parsing por regex abaixo (_extract_diagnosis_from_raw_message)
        # deixa de ser o caminho principal e vira o ULTIMO fallback, so
        # usado quando structured_response nao vem preenchido.
        react_agent = create_react_agent(llm, tools=[web_tool], response_format=DiagnosisModel)
        messages = {"messages": [{"role": "user", "content": prompt + json_instruction}]}
        config = {"callbacks": [_langfuse_handler]}
        try:
            return react_agent.invoke(messages, config=config)
        except TRANSPORT_FAILURE_EXCEPTIONS:
            # Nao e um problema do structured output - e o provider
            # inalcancavel. Deixa subir sem tratamento especial, para o
            # AI Gateway (invoke_via_gateway, DA-26) decidir circuit
            # breaker/fallback normalmente, exatamente como antes desta
            # mudanca.
            raise
        except Exception as exc:  # noqa: BLE001
            # A chamada ADICIONAL de structured output falhou por algum
            # motivo especifico de aplicacao (ex: o modelo nao suporta
            # tool-calling bem o suficiente para with_structured_output,
            # ou devolveu algo que nao bate com o schema). Refaz o MESMO
            # ReAct sem response_format - volta pro comportamento de
            # antes desta mudanca (texto + parsing por regex abaixo),
            # em vez de deixar a requisicao inteira quebrar por causa de
            # uma camada que deveria so melhorar a qualidade, nao ser um
            # ponto novo de falha.
            logging.getLogger(__name__).warning(
                "structured output (response_format=DiagnosisModel) falhou, "
                "refazendo sem ele - caindo no parsing por regex: %s",
                exc,
            )
            react_agent_plain = create_react_agent(llm, tools=[web_tool])
            return react_agent_plain.invoke(messages, config=config)

    # AI Gateway v1 (DA-26): centraliza Hybrid Inference (DA-20) +
    # policy de roteamento por sensibilidade de dado + circuit breaker
    # + budget - ver app/llm/gateway.py. prompt_text so alimenta a
    # estimativa de custo, nao afeta a chamada em si.
    react_result, llm_provider_used = invoke_via_gateway(
        _build_and_invoke, state=state, prompt_text=prompt, model_name=model_name
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

    # Avaliacao externa (curto prazo, item 5): structured_response e
    # populado por create_react_agent quando response_format=DiagnosisModel
    # (ver _build_and_invoke acima) teve sucesso - ja e uma instancia
    # validada de DiagnosisModel, nao um texto pra adivinhar. So cai no
    # parsing por regex (o comportamento INTEIRO de antes desta mudanca,
    # preservado abaixo sem alteracao) quando structured_response nao
    # veio - response_format ausente/falhou, ou (chamada direta a este
    # node em algum teste) um resultado que nao passou por
    # create_react_agent com response_format.
    structured = react_result.get("structured_response")
    if structured is not None:
        diagnosis = (
            structured.model_dump() if hasattr(structured, "model_dump") else dict(structured)
        )
    else:
        # Extrai JSON estruturado da resposta final do agente
        json_match = re_module.search(
            r'\{[^{}]*"probable_root_cause"[^{}]*\}', raw, re_module.DOTALL
        )
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
    diagnosis["llm_provider_used"] = llm_provider_used
    return diagnosis


@observe(name="sap_specialist")
def sap_diagnosis_node(state: CopilotState) -> CopilotState:
    """Sub-agente especialista SAP - roteado pelo supervisor quando
    `agent_domain == "sap"` (ver app/agent/supervisor.py)."""
    return {"diagnosis": _run_diagnosis_agent(state, _SAP_SPECIALIST_PERSONA)}


@observe(name="saas_specialist")
def saas_diagnosis_node(state: CopilotState) -> CopilotState:
    """Sub-agente especialista multi-fornecedor (SaaS empresarial +
    generalista) - roteado pelo supervisor quando `agent_domain` e
    "saas" ou "generic" (nenhum dominio SAP identificado)."""
    return {"diagnosis": _run_diagnosis_agent(state, _ENTERPRISE_SPECIALIST_PERSONA)}


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

    # DA-25: evidencias montadas deterministicamente (nunca citadas
    # pelo LLM) - ver _assemble_evidence().
    def _evidence_line(item: dict) -> str:
        locator_suffix = f" ({item['locator']})" if item.get("locator") else ""
        return f"- [{item['trust_level']}] {item['source_type']}{locator_suffix}"

    evidence_items = _assemble_evidence(state)
    evidence_md = (
        "\n".join(_evidence_line(item) for item in evidence_items)
        if evidence_items
        else "- (nenhuma evidencia disponivel)"
    )

    report = f"""## Diagnostico do Incidente

**Descricao reportada:** {state["description"]}
{connector_line}
**Causa raiz provavel:** {diagnosis.get("probable_root_cause", "N/A")}

**Confianca:** {diagnosis.get("confidence", 0.0):.0%} (evidence_strength: {diagnosis.get("evidence_strength", 0.0):.0%}, LLM: {diagnosis.get("llm_provider_used", "N/A")}, agente: {state.get("agent_domain", "N/A")})

**Documento usado como base:** {matched}

**Proximos passos:**
{next_steps_md if next_steps_md else "- (nenhum passo sugerido)"}

**Fontes recuperadas (candidatas):** {sources}

**Evidencias (DA-25 - trust_level por tipo de fonte, nao autoavaliado pelo LLM):**
{evidence_md}
"""
    return {"report_markdown": report}
