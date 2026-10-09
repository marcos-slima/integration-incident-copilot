"""Nodes do grafo LangGraph do SAP Integration Copilot.

Cada node e uma funcao pura que recebe e retorna CopilotState.
Instrumentado com Langfuse via @observe.
"""

import json
import logging
import os
import re
import re as re_module
from uuid import uuid4

from app.config import settings
from app.metrics import RULE_ENGINE_HIT_TOTAL

if settings.langfuse_configured:
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
    os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)
    os.environ.setdefault("LANGFUSE_BASE_URL", settings.langfuse_host)
else:
    # Sem as DUAS chaves o @observe AINDA assim cria um client default e
    # tenta exportar a cada span, logando "Authentication error: Langfuse
    # client / LANGFUSE_PUBLIC_KEY environment" a cada node (6+ por
    # diagnostico). A SDK v4 le este flag em client.py:365, entao e o
    # desligamento suportado - e precisa vir AQUI, antes de qualquer
    # client/span ser criado.
    os.environ["LANGFUSE_TRACING_ENABLED"] = "false"

from ddgs import DDGS
from langchain_core.tools import tool as lc_tool
from langfuse import Langfuse, get_client, observe
from langfuse.langchain import CallbackHandler
from langgraph.prebuilt import create_react_agent

from app.agent import prompts
from app.agent.rules import match_known_error
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

# DA-57: a fonte de busca web aprovada por `interface_type` deixou de ser
# literal em codigo. Os dois mapas que viviam aqui
# (`_WEB_SEARCH_SITE_MAP` com 8 entradas e o dict `tech_term` com 8) foram
# para a tabela `web_search_sources`, e a resolucao e FAIL-CLOSED:
# `resolve_approved_source()` devolve None quando nao ha linha habilitada
# e a busca web NAO acontece. Sem fallback em codigo — um default
# hardcoded seria a falha silenciosa que a DA-57 removeu: um conector novo
# aceito em todo o produto, e jogado num filtro generico sem ninguem ver.
#
# Consequencia que vale registrar: `successfactors` (DA-34) e `po` (DA-56)
# nunca estiveram em nenhum dos dois mapas, e perdiam tambem o `tech_term`
# (cai no generico "SAP integration"), entao a identidade do conector se
# perdia ANTES do filtro de site. A migration 008 semeia as 10 linhas.
#
# Ver app/services/web_search_sources.py e
# app/admin/models.py::WebSearchSource.

# Avaliacao externa (medio prazo, item 4): inicializa o client Langfuse
# EXPLICITAMENTE com mask=redact_pii_deep, ANTES de qualquer
# CallbackHandler()/@observe rodar - "get_client()" so cria o client
# default (sem mask) se nenhum ja existir, entao a ordem aqui importa.
# Isso cobre "antes do Langfuse": o SDK aplica essa mascara a QUALQUER
# input/output que @observe capturar automaticamente (o CopilotState
# inteiro, nao so o texto que sanitize_untrusted_input ja sanitizava
# manualmente para o prompt).
#
# Guard: sem as DUAS chaves, nao criamos client nenhum. Criar client com
# chave vazia faz o SDK tentar exportar em background e logarithm
# "401 Unauthorized" a cada run (o @observe acima vira no-op, o que e
# desejado). A mascara de PII nao e perdida: ela so importa quando existe
# client exportando span, e sem chave nao exporta span nenhum.
if settings.langfuse_configured:
    Langfuse(mask=redact_pii_deep)
    _langfuse_handler = CallbackHandler()
    observe_span = observe
else:

    def observe_span(*_args, **_kwargs):
        """No-op stand-in para @observe quando o Langfuse nao esta
        configurado.

        Nao basta desligar `LANGFUSE_TRACING_ENABLED`: o SDK v4 loga
        "client initialized without public_key" no __init__ do client, e
        o proprio @observe chama get_client() para pegar o tracer - ou
        seja, o aviso sai mesmo com tracing desligado. O unico jeito de
        nao instanciar client e nao decorating as funcoes. Como o grafo
        nao depende de span para funcionar, o no-op e semanticamente
        identico, so que silencioso.

        Usado pelos nodes daqui e por app/agent/graph.py.
        """

        def _decorator(fn):
            return fn

        return _decorator

    _langfuse_handler = None

MAX_LOGS_IN_PROMPT = 3_000
MAX_PAYLOAD_IN_PROMPT = 3_000

_logger = logging.getLogger(__name__)


def connector_node(state: CopilotState) -> CopilotState:
    interface_type = state.get("interface_type")
    if not interface_type:
        return {"connector_data": None}

    connector = get_connector(interface_type)
    result = connector.fetch(state.get("identifier") or "")

    # A-09: incrementa metrica de chamadas a conectores externos
    from app.metrics import CONNECTOR_REQUEST_TOTAL

    _metric_status = (
        "mock" if result.is_mock else ("error" if result.status == "error" else "success")
    )
    CONNECTOR_REQUEST_TOTAL.labels(
        connector=result.source_system,
        status=_metric_status,
    ).inc()

    return {"connector_data": result}


def _effective_query(state: CopilotState) -> str:
    base = state["description"]
    data = state.get("connector_data")
    if data:
        return f"{base}\n{data.message}"
    return base


@observe_span(name="retrieve")
def retrieve_node(state: CopilotState) -> CopilotState:
    hits = retrieve(_effective_query(state), target="incidents", top_k=3)
    return {"retrieved_context": hits}


@observe_span(name="graph_enrich")
def ontology_enrich_node(state: CopilotState) -> CopilotState:
    """So entra no grafo quando ONTOLOGY_ENRICHMENT_ENABLED=true - consulta
    a ontologia SKOS (error_codes.ttl) por upper categories e next steps,
    sem depender de Neo4j (usa rdflib direto no TTL).

    DA-61: ontology enrichment e graph enrichment sao independentes.
    GraphRAG (Neo4j) serve para recuperar incidentes anteriores; ontology
    enrichment servico para inferencia semantica (skos:broader*, next_steps).
    Ambos podem rodar juntos ou separados."""
    try:
        from app.ontology.enrichment import (
            get_next_steps_from_category,
            get_upper_categories_from_category,
        )

        # Extract error category from diagnosis (rule_engine_category)
        category = state.get("diagnosis", {}).get("rule_engine_category")
        if not category:
            return {"ontology_inference": "", "ontology_next_steps": ""}

        # Query TTL via rdflib for upper categories
        upper_cats = get_upper_categories_from_category(category)
        next_steps = get_next_steps_from_category(category)

        # Format upper categories as text
        upper_context = (
            "\n".join([f"- {cat}" for cat in upper_cats])
            if upper_cats
            else "Nenhuma upper category definida"
        )
        next_steps_context = (
            "\n".join([f"- {step}" for step in next_steps])
            if next_steps
            else "- Nenhum next step definido"
        )

        return {
            "ontology_inference": upper_context,
            "ontology_next_steps": next_steps_context,
        }
    except (FileNotFoundError, ValueError) as exc:
        _logger.warning(
            "ontology_enrich_node: falha ao carregar/consultar error_codes.ttl: %s",
            exc,
        )
        return {"ontology_inference": "", "ontology_next_steps": ""}


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


@observe_span(name="graph_write")
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
            confidence=float(diagnosis.get("model_confidence", diagnosis.get("confidence", 0.0))),
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


def _web_search_allowed(state) -> bool:
    """P0.2 + DA-57: gate unico de egress para busca web - usado pelo
    web_search_node e pelo tool do agente ReAct. Exige web_search_enabled
    (interruptor principal), que WEB_SEARCH_POLICY permita E que exista uma
    fonte APROVADA (linha habilitada em `web_search_sources`) para o
    `interface_type` do incidente.

    DA-57: o ramo `approved` antes retornava `True` incondicionalmente — o
    nome prometia uma lista de sites aprovados que nao existia em lugar
    nenhum do codigo. Agora `approved` significa o que diz: so passa quem
    tem linha habilitada. Sem linha => sem busca web, e a fonte e' lida do
    banco (fail-closed), nao de um default em codigo.
    """
    from app.llm.gateway import classify_sensitivity
    from app.services.web_search_sources import resolve_approved_source

    if not settings.web_search_enabled:
        return False
    policy = settings.web_search_policy
    if policy == "disabled":
        return False
    if policy == "public_only" and classify_sensitivity(state) != "public":
        return False
    # Unica fonte de verdade da query: a linha aprovada. Sem ela, o
    # interface_type nao tem onde buscar — e nao ha tech_term generico
    # para cair (era exatamente o que escondia successfactors e po).
    return resolve_approved_source(state.get("interface_type")) is not None


@observe_span(name="web_search")
def web_search_node(state: CopilotState) -> CopilotState:
    """Busca web via DuckDuckGo - ativada apenas quando o RAG local
    nao encontrou contexto suficiente (todos os hits com score baixo
    ou nenhum hit). Direciona a busca para SAP Community e GitHub SAP
    para resultados mais relevantes ao contexto SAP/integracao.

    Privacidade (P0.2, 23/09/2026): a query nunca contem a descricao
    original do incidente — apenas tech_term (derivado de interface_type)
    + mensagem sanitizada do conector (sem PII) + site_filter.

    Politica de egress controlada por WEB_SEARCH_POLICY:
      disabled    — nunca executa
      approved    — executa com query sanitizada (default; WEB_SEARCH_ENABLED
                    continua sendo o interruptor principal, default off)
      public_only — so executa se classificacao de sensibilidade = 'public'

    So ativa quando threshold do RAG local nao foi atingido."""
    hits = state.get("retrieved_context", [])
    top_score = hits[0]["score"] if hits else 0.0

    if not _web_search_allowed(state) or top_score >= settings.web_search_threshold:
        return {"web_search_results": []}

    from app.services.web_search_sources import resolve_approved_source

    # DA-57: fonte aprovada vem do banco (fail-closed). `_web_search_allowed`
    # ja garantiu que existe; o segundo check cobre a corrida em que a linha
    # foi desabilitada entre as duas leituras.
    source = resolve_approved_source(state.get("interface_type"))
    if source is None:
        return {"web_search_results": []}
    site_filter = source.site_filter
    tech_term = source.tech_term

    # P0.2 (revisao arquitetural externa, 23/09/2026): a descricao ORIGINAL do
    # incidente nunca vai para a rede — pode conter nome de cliente, sistema,
    # ambiente, IDoc, RFC, endpoint, stack trace (informacao corporativa interna).
    # A query e construida SOMENTE a partir de:
    #   1. tech_term  — termo tecnico derivado do interface_type (enum, nao dado livre)
    #   2. connector_data.message — mensagem de erro do conector, ja sanitizada via
    #      sanitize_untrusted_input antes de chegar aqui; redact_pii_text garante
    #      defesa em profundidade (mesma funcao aplicada em _make_web_search_tool)
    #   3. site_filter — filtro de dominio SAP (nao dado do incidente)
    # Isso garante que a busca web receba apenas termos genericos/tecnicos, nunca
    # contexto corporativo especifico do cliente.
    connector_message = ""
    data = state.get("connector_data")
    if data and data.message:
        connector_message = redact_pii_text(str(data.message))

    query = f"{tech_term} {connector_message} {site_filter}".strip()

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


# Padroes de prompt injection (SEC-04, validacao 2026-10-07).
#
# Os padroes sao escritos em ASCII minusculo e casam contra uma copia
# NORMALIZADA do texto (_normalize_for_detection): NFKC (largura total,
# ligaduras), sem diacriticos ("instruções" -> "instrucoes", "você" ->
# "voce") e sem caracteres de largura zero. A substituicao volta para as
# posicoes do texto ORIGINAL - o resto do texto chega ao LLM intacto.
# Antes so o ASCII exato casava: "Ignore as instruções anteriores" (com
# acento, imperativo) e "ｉｇｎｏｒｅ previous instructions" passavam.
#
# Lista de bloqueio continua sendo defesa em profundidade, nao protecao: o
# isolamento estrutural (dado nao confiavel fora do papel da instrucao)
# depende de mudar o prompt e de medir no promptfoo (gate
# prompt_digest_measured, DA-53).
_PT_VERBO_IGNORAR = (
    r"(?:ignor(?:e|a|ar|em)|desconsider(?:e|a|ar|em)|esquec(?:a|e|er|am)"
    r"|desprez(?:e|a|ar)|descart(?:e|a|ar))"
)
_PT_OBJETO = (
    r"(?:(?:tod[ao]s?\s+)?(?:as|os|suas|tuas|estas|essas)\s+)?"
    r"(?:instruc(?:ao|oes)|comandos?|regras?|orientac(?:ao|oes)|diretrizes?|prompts?)"
    r"(?:\s+(?:anteriores|acima|previas|precedentes|iniciais|do\s+sistema|de\s+sistema))?"
)
_INJECTION_PATTERNS = [
    # --- ingles ---
    (
        r"(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+)?(?:the\s+|your\s+)?"
        r"(?:previous|prior|above|earlier|preceding|system)\s+(?:instructions?|prompts?|rules|commands)"
    ),
    r"ignore\s+all\s+previous\s+commands",
    r"you\s+are\s+now\s+(?:a|an|the)\b",
    r"from\s+now\s+on,?\s+you\s+(?:are|will|must)",
    r"act\s+as\s+(?:a\s+)?(?:different|new|another)",
    r"pretend\s+(?:to\s+be|you\s+are)",
    r"new\s+instructions?\s*:",
    r"system\s*:\s*you",
    r"\[\s*system\s*\]",
    r"<\s*/?\s*system\s*>",
    (
        r"(?:print|reveal|show|repeat|output)\s+(?:me\s+)?(?:all\s+)?(?:your\s+|the\s+)?"
        r"(?:system\s+)?(?:prompt|instructions?|context)"
    ),
    r"you\s+must\s+now\s+(?:output|generate|create|respond)",
    r"you\s+are\s+a\s+(?:hacker|attacker|enemy)",
    r"dan\s+mode",
    r"developer\s+mode",
    r"jailbreak",
    # --- portugues (normalizado: sem acento) ---
    _PT_VERBO_IGNORAR + r"\s+" + _PT_OBJETO,
    _PT_VERBO_IGNORAR + r"\s+tudo(?:\s+(?:acima|o\s+que\s+foi\s+dito|anterior|antes))?",
    (
        r"(?:a\s+partir\s+de\s+agora,?\s+)?voce\s+(?:agora\s+)?(?:e|sera|passa\s+a\s+ser)"
        r"\s+(?:um|uma|o|a)\b"
    ),
    r"a\s+partir\s+de\s+agora,?\s+(?:voce|responda|aja|atue)",
    r"(?:aja|atue|atuar|agir)\s+como\s+(?:se\s+fosse\s+)?(?:um|uma|o|a)\b",
    r"finja\s+(?:ser|que\s+(?:e|voce))",
    r"novas?\s+instruc(?:ao|oes)\s*:",
    r"instruc(?:ao|oes)\s+(?:anteriores|precedentes|acima)\s*:",
    (
        r"(?:imprim(?:a|ir)|revel(?:e|ar)|mostr(?:e|ar)|repit(?:a|ir)|exib(?:a|ir))\s+(?:me\s+)?"
        r"(?:o\s+|seu\s+|suas?\s+|as\s+)?(?:prompt|instruc(?:ao|oes)|contexto)"
    ),
    r"voce\s+(?:deve|precisa)\s+(?:agora\s+)?(?:gerar|criar|emitir|responder)",
    r"modo\s+(?:dan|desenvolvedor)",
    r"quebra\s+de\s+restric(?:ao|oes)",
    # --- espanhol (LATAM) ---
    r"(?:ignora|olvida|descarta)\s+(?:todas\s+)?(?:las\s+)?instrucciones\s+(?:anteriores|previas)",
    r"ahora\s+eres\s+(?:un|una)\b",
]

_INJECTION_RE = None
_INVISIBLE = frozenset("\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u00ad")


def _get_injection_re():
    global _INJECTION_RE
    if _INJECTION_RE is None:
        _INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in _INJECTION_PATTERNS))
    return _INJECTION_RE


def _normalize_for_detection(
    text: str, invisivel_vira_espaco: bool = False
) -> tuple[str, list[int]]:
    """Copia normalizada (NFKC, sem diacritico, minuscula) e, para cada
    caractere dela, o indice do caractere de origem. Caracteres de largura
    zero somem ("ign\u200bore") ou viram espaco ("previous\u200binstructions");
    sanitize_untrusted_input testa as duas formas."""
    import unicodedata

    chars: list[str] = []
    origem: list[int] = []
    for i, ch in enumerate(text):
        if ch in _INVISIBLE:
            if invisivel_vira_espaco:
                chars.append(" ")
                origem.append(i)
            continue
        base = unicodedata.normalize("NFKC", ch)
        for c in unicodedata.normalize("NFD", base):
            if unicodedata.combining(c):
                continue
            for low in c.lower():
                chars.append(low)
                origem.append(i)
    return "".join(chars), origem


def sanitize_untrusted_input(text: str | None, field_name: str = "input") -> str:
    """Sanitiza entrada nao confiavel antes de incluir no prompt LLM.

    Neutraliza padroes conhecidos de prompt injection (ver comentario de
    _INJECTION_PATTERNS) e redige PII. Defesa em profundidade, nao
    protecao completa. Campos tratados: description, logs, payload,
    connector_data, chunks do RAG (PDFs externos) e resultados de busca web.

    Args:
        text: Texto a sanitizar
        field_name: Nome do campo (para logging)

    Returns:
        Texto sanitizado, ou string vazia se None
    """
    if not text:
        return ""

    trechos: list[tuple[int, int]] = []
    for como_espaco in (False, True):
        normalizado, origem = _normalize_for_detection(text, como_espaco)
        for m in _get_injection_re().finditer(normalizado):
            if m.end() > m.start():
                trechos.append((origem[m.start()], origem[m.end() - 1] + 1))

    if trechos:
        partes: list[str] = []
        cursor = 0
        for ini, fim in sorted(trechos):
            ini = max(ini, cursor)  # sobreposicao com o trecho anterior
            partes.append(text[cursor:ini])
            if fim > ini:
                partes.append("[CONTEUDO_REMOVIDO_INJECTION]")
            cursor = max(cursor, fim)
        partes.append(text[cursor:])
        text = "".join(partes)
        logging.getLogger(__name__).warning(
            "Possivel prompt injection detectado no campo '%s' — conteudo neutralizado",
            field_name,
        )

    # Caracteres invisiveis restantes saem do texto que vai ao LLM.
    text = "".join(ch for ch in text if ch not in _INVISIBLE)

    # Avaliacao externa (medio prazo, item 4): redaction de PII "antes do
    # prompt" - e-mail/CPF/numero de IDoc nunca chegam ao LLM (app/redaction.py).
    return redact_pii_text(text)


# Linhas de excecao relevantes em stack traces SAP/Java/Groovy/XSLT —
# capturar essas linhas antes de truncar garante que o LLM veja o que
# importa mesmo quando o log e grande. Ordem importa: as linhas mais
# especificas (SAP fault, HTTP status, iFlow) ficam primeiro.
_EXCEPTION_LINE_RE = re.compile(
    r"^.*(?:"
    r"Caused by|"
    r"Exception|"
    r"Error:|"
    r"SAP Fault|"
    r"SAP_BASIS_ERR|"
    r"HTTP [45]\d{2}|"
    r"MAPPING_FAIL|"
    r"XSLT_PARS|"
    r"iflow|"
    r"Message Processing Log"
    r").*$",
    re.IGNORECASE | re.MULTILINE,
)
_MAX_EXCEPTION_LINES = 30


def _smart_truncate(text: str, limit: int) -> str:
    """Truncagem inteligente para stack traces e logs de integracao SAP.

    Estrategia:
    1. Se o texto cabe no limite -> devolve inteiro.
    2. Extrai linhas de excecao/erro relevantes (Caused by, HTTP 4xx/5xx,
       SAP Fault, etc.) via regex — capped em _MAX_EXCEPTION_LINES.
    3. Se o extracto relevante cabe no limite -> usa so ele, com cabecalho
       indicando que o log foi filtrado.
    4. Caso contrario -> trunca o extracto no limite (raro; seria um log
       com dezenas de excecoes aninhadas).

    Avaliacao externa (Fase 1 DA-30): "Estrategia de Truncagem Inteligente
    para Stack Traces — extrair apenas as linhas relevantes de excecao
    (Caused by, SAP Fault Details, HTTP Response Code)".
    """
    if len(text) <= limit:
        return text

    matches = _EXCEPTION_LINE_RE.findall(text)
    if not matches:
        # Sem linhas de excecao reconhecidas — truncagem simples.
        return text[:limit] + f"\n[...truncado - {len(text) - limit} chars omitidos...]"

    relevant = matches[:_MAX_EXCEPTION_LINES]
    excerpt = "\n".join(relevant)
    header = (
        f"[log filtrado: {len(text)} chars -> {len(relevant)}/{len(matches)} "
        f"linhas relevantes extraidas]\n"
    )
    full = header + excerpt

    if len(full) <= limit:
        return full
    return full[:limit] + f"\n[...truncado - {len(full) - limit} chars omitidos...]"


def _truncate(text: str, limit: int) -> str:
    """Alias de compatibilidade — delega para _smart_truncate.
    Mantido para nao quebrar referencias existentes ao _truncate simples."""
    return _smart_truncate(text, limit)


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
    if data and data.is_fallback:
        # Identificador nao reconhecido: o conector devolve um resultado
        # GENERICO (em alguns mocks, ex. OData/RFC, com status=error e
        # codigo 500 fabricados). Esses campos NAO vao para o prompt -
        # em teste real o modelo construiu a causa raiz em cima do "500"
        # simulado, mesmo com o aviso de fallback. Sem o dado inventado,
        # o modelo so pode se apoiar na descricao, nos logs e no RAG.
        connector_block = f"""
Dados do sistema (conector {data.source_system}): NENHUM DADO DISPONIVEL - o
identificador informado nao foi reconhecido pelo sistema. Nao existe status
nem codigo de erro observado; nao presuma nenhum. Baseie o diagnostico apenas
na descricao, nos logs e no documento de contexto, use confidence baixa
(< 0.4) e considere matched_source como null se o documento nao corresponder
claramente ao incidente.
"""
    elif data:
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
  detalhe bruto: {safe_raw}
"""

    _raw_graph_block = format_graph_context_for_prompt(state.get("graph_history", []))
    graph_block = (
        sanitize_untrusted_input(_raw_graph_block, "graph_context") if _raw_graph_block else ""
    )

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

    # DA-53: o template e' dado (app/agent/prompts.py), nao f-string local.
    # `safe_description` ja vem sanitizado acima; `render` exige todos os
    # slots e falha alto, para que um bloco de contexto nunca desapareca
    # do prompt em silencio.
    return prompts.render(
        persona=persona,
        description=safe_description,
        extras=extras,
        connector_block=connector_block,
        context_block=context_block,
        others_note=others_note,
        graph_block=graph_block,
        web_block=web_block,
    )


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
    rag_score = (
        float(top_hit.get("rerank_score_calibrated", top_hit.get("score", 0.0))) if top_hit else 0.0
    )  # DA-42: usa sigmoid calibrado

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
      OU regra curada do rule engine (DA-33) — confianca maxima
    - simulated: dado de conector mock/fallback (nao um sistema real)
    - retrieved_document: chunk RAG (Qdrant, ja passado pelo reranker)
      ou historico do GraphRAG
    - web_untrusted: resultado de busca web (DuckDuckGo), nao curado
    - user_reported: a propria descricao textual do incidente - nunca
      verificada de forma independente, e o sinal mais fraco de todos.
    """
    evidence: list[dict] = []

    # DA-33 + DA-25: quando o rule engine resolveu o incidente deterministicamente,
    # registra como evidencia de maxima confianca (nao e dado de usuario,
    # nao e RAG, nao e web — e uma regra curada, conhecimento incorporado).
    diagnosis = state.get("diagnosis") or {}
    if str(diagnosis.get("llm_provider_used", "")).startswith("rule_engine"):
        category = diagnosis.get("rule_engine_category", "unknown")
        RULE_ENGINE_HIT_TOTAL.labels(rule_id=category).inc()
        evidence.append(
            {
                "source_id": f"rule_engine:{category}",
                "source_type": "rule_engine",
                "locator": category,
                "excerpt": diagnosis.get("probable_root_cause", "")[:500],
                "retrieval_score": diagnosis.get("confidence"),
                "rerank_score": None,
                "trust_level": "system_observed",  # regra curada = confianca maxima
            }
        )

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
                "rerank_score_calibrated": hit.get("rerank_score_calibrated"),  # DA-42
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
    LLM nem so na validacao de schema.

    P1.5 (revisao arquitetural externa, 23/09/2026): confidence renomeado
    para model_confidence (auto-relatado pelo LLM, ajustado pelos guardrails)
    e novo campo diagnosis_confidence calculado deterministicamente pelo
    pipeline (nao depende de autoavaliacao do LLM).

    O campo interno "confidence" do DiagnosisModel (saida do LLM) e mapeado
    para model_confidence aqui. O caller (run_diagnosis em graph.py) recebe
    o dict com ambos os campos preenchidos.
    """
    # Normaliza a confianca reportada pelo LLM para [0.0, 1.0]
    raw_model_confidence = float(
        diagnosis.pop("confidence", diagnosis.get("model_confidence", 0.0))
    )
    model_confidence = max(0.0, min(1.0, raw_model_confidence))

    # Diagnostico do Rule Engine (DA-33): "rule_engine:<categoria>" nao e
    # documento RAG, e sim a regra deterministica que casou. So rules.py
    # define rule_engine_category (a saida do LLM nao tem esse campo), entao
    # o LLM nao consegue se passar pelo rule engine.
    claimed_source = diagnosis.get("matched_source")
    is_rule_engine = bool(diagnosis.get("rule_engine_category")) and claimed_source == (
        f"rule_engine:{diagnosis.get('rule_engine_category')}"
    )

    # Validacao 2026-10-07 (M-03): a forca de evidencia da regra (0.95 com
    # dado real de conector, 0.70 so com texto - rules.py) era SOBRESCRITA
    # aqui pelo calculo de RAG/conector, que da 0.0 quando o rule engine
    # resolve sem RAG - e o teto derrubava a confianca da regra para ~0.
    # Um unico calculo: o maior entre o sinal do pipeline e o da regra.
    rule_evidence = float(diagnosis.get("evidence_strength") or 0.0) if is_rule_engine else 0.0
    evidence_strength = max(_compute_evidence_strength(state), rule_evidence)
    diagnosis["evidence_strength"] = round(evidence_strength, 3)

    evidence_ceiling = min(1.0, evidence_strength + EVIDENCE_CONFIDENCE_MARGIN)
    model_confidence = min(model_confidence, evidence_ceiling)

    data = state.get("connector_data")
    if data and data.is_fallback:
        capped = min(model_confidence, 0.4)
        if capped < model_confidence:
            model_confidence = capped
            diagnosis["probable_root_cause"] = (
                f"[confianca limitada - identificador nao reconhecido pelo sistema] "
                f"{diagnosis.get('probable_root_cause', '')}"
            )

    if not state.get("retrieved_context") and not data and not is_rule_engine:
        capped = min(model_confidence, 0.3)
        if capped < model_confidence:
            model_confidence = capped
            diagnosis["matched_source"] = None
            diagnosis["probable_root_cause"] = (
                f"[confianca limitada - nenhum documento relevante encontrado] "
                f"{diagnosis.get('probable_root_cause', '')}"
            )

    # Valida matched_source contra as fontes realmente recuperadas
    # Impede que o LLM invente ou alucine um nome de documento.
    # Validacao 2026-10-07 (M-04): a checagem so rodava quando HAVIA
    # documentos recuperados. Com conector real e RAG vazio, um nome de
    # arquivo inventado pelo LLM passava intacto com confianca alta. Sem
    # documento recuperado, nenhuma fonte citada e valida.
    retrieved = state.get("retrieved_context") or []
    valid_sources = {h["source"] for h in retrieved if h.get("source")}
    if claimed_source and claimed_source not in valid_sources and not is_rule_engine:
        diagnosis["matched_source"] = None
        model_confidence = min(model_confidence, 0.3)
        diagnosis["probable_root_cause"] = (
            f"[matched_source '{claimed_source}' nao esta entre os documentos recuperados - "
            f"confianca limitada] "
            f"{diagnosis.get('probable_root_cause', '')}"
        )

    diagnosis["model_confidence"] = round(model_confidence, 3)

    # A2: diagnosis_confidence — metrica CALCULADA (nao auto-relatada pelo LLM).
    #
    # BUG ANTERIOR: max(evidence_strength, model_confidence * evidence_strength)
    # simplifica para evidence_strength * max(1, model_confidence) = evidence_strength
    # porque model_confidence esta sempre em [0, 1]. A metrica ignorava
    # completamente model_confidence, embora o campo fosse recomendado
    # para automacao.
    #
    # FORMULA CORRIGIDA: produto das duas sinalizacoes independentes.
    # diagnosis_confidence = evidence_strength * model_confidence
    #
    # Interpreta: "quao confiavel e este diagnostico dado o que o pipeline
    # efetivamente encontrou E o que o LLM avaliou".
    # - evidence_strength = 0 → diagnosis_confidence = 0 (sem contexto, sem confianca)
    # - model_confidence  = 0 → diagnosis_confidence = 0 (LLM nao confia, sem confianca)
    # - Ambos = 1.0        → diagnosis_confidence = 1.0 (maxima confianca)
    # - Qualquer um baixo  → puxa o resultado para baixo (comportamento desejado)
    #
    # Nao afirma calibracao probabilistica: e uma heuristica para ranking
    # relativo de diagnosticos, nao uma probabilidade formal.
    diagnosis["diagnosis_confidence"] = round(evidence_strength * model_confidence, 3)

    return diagnosis


# A3: politica de egress para busca web — bloqueia padroes que indicam
# dados corporativos que o LLM pode ter incluido na query inadvertidamente.
# redact_pii_text cobre e-mail/CPF/CNPJ/telefone (via regex). Esta
# camada adicional cobre: URLs completas, numeros de IDoc (18 digitos),
# client SAP (3-4 digitos isolados), GUIDs e tokens longos.
# Limitamos tambem o tamanho da query para evitar exfiltrar payloads.
_WEB_SEARCH_EGRESS_PATTERNS = re_module.compile(
    r"""
    https?://[^\s]+                         # URLs completas
    | \b\d{18}\b                            # numeros IDoc SAP (18 digitos)
    | \b[0-9a-fA-F]{32}\b                   # MD5 / GUID sem hifens
    | \b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-      # UUID com hifens
      [0-9a-fA-F]{4}-[0-9a-fA-F]{4}-
      [0-9a-fA-F]{12}\b
    | \b[A-Z]{2,3}\d{8,12}\b               # codigos de documento SAP (ex: SO0000012345)
    | Bearer\s+\S+                          # tokens Bearer
    | Basic\s+[A-Za-z0-9+/=]+              # tokens Basic Auth
    """,
    re_module.VERBOSE,
)
_WEB_SEARCH_MAX_QUERY_CHARS = 200


def _sanitize_web_search_query(query: str) -> str:
    """Aplica politica de egress a uma query de busca web antes de enviar
    para DuckDuckGo. Camada de defesa em profundidade sobre redact_pii_text:
    remove URLs, IDs numericos longos, GUIDs e tokens de autenticacao.
    Trunca a query para evitar exfiltrar blocos de payload.
    """
    # 1) PII (e-mail, CPF, CNPJ, telefone)
    sanitized = redact_pii_text(query)
    # 2) Padroes corporativos especificos de SAP/integracao
    sanitized = _WEB_SEARCH_EGRESS_PATTERNS.sub("[REDACTED]", sanitized)
    # 3) Limita comprimento para evitar exfiltracao de payloads longos
    if len(sanitized) > _WEB_SEARCH_MAX_QUERY_CHARS:
        sanitized = sanitized[:_WEB_SEARCH_MAX_QUERY_CHARS]
        logging.getLogger(__name__).debug(
            "[web_search] query truncada em %d chars para politica de egress.",
            _WEB_SEARCH_MAX_QUERY_CHARS,
        )
    return sanitized.strip()


def _make_web_search_tool(state):
    """Fabrica um tool de busca web contextualizado com o interface_type
    do incidente — o agente ReAct decide quando chamar.

    DA-57: `site_filter` vem da linha aprovada em `web_search_sources`. Sem
    linha habilitada nao ha tool — o ReAct recebe uma tool que diz explicitamente
    que nao ha fonte aprovada, em vez de uma que buscaria num filtro generico.
    """
    from app.services.web_search_sources import resolve_approved_source

    source = resolve_approved_source(state.get("interface_type"))

    if source is None:

        @lc_tool
        def web_search_tool(query: str) -> str:
            """Busca web indisponivel para este incidente (DA-57, fail-closed)."""
            return (
                "Busca web indisponivel: nao ha fonte aprovada cadastrada para o "
                "interface_type deste incidente. Responda a partir do RAG local, do "
                "conector e das regras — nao invente fonte externa."
            )

        return web_search_tool

    site_filter = source.site_filter

    @lc_tool
    def web_search_tool(query: str) -> str:
        """Busca informacao tecnica sobre incidente SAP em SAP Community e SAP Help.
        Use apenas termos tecnicos genericos — nunca dados sensiveis do cliente.

        Args:
            query: Termos tecnicos de busca (ex: 'BAPI_MATERIAL_SAVEDATA authorization error')
        """
        # A3: politica de egress aplicada antes de qualquer chamada de rede.
        # _sanitize_web_search_query combina redact_pii_text (e-mail/CPF/CNPJ)
        # com remocao de URLs, IDoc/IDs numericos longos, GUIDs e tokens Bearer/Basic,
        # mais truncamento a 200 chars. Defesa em profundidade: nao depende
        # apenas da instrucao de prompt acima ("nunca dados sensiveis").
        safe_query = _sanitize_web_search_query(query)
        try:
            with DDGS() as ddgs:
                hits = list(ddgs.text(f"{safe_query} {site_filter}", max_results=5))
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
# DA-53: personas e template do prompt vivem em app/agent/prompts.py, com
# versao e digest. Aqui ficam so os aliases, porque varios testes e o
# proprio grafo referenciam os nomes com prefixo `_`.
_SAP_SPECIALIST_PERSONA = prompts.SAP_SPECIALIST_PERSONA
_ENTERPRISE_SPECIALIST_PERSONA = prompts.ENTERPRISE_SPECIALIST_PERSONA
_GENERIC_INTEGRATION_PERSONA = prompts.GENERIC_INTEGRATION_PERSONA


def _run_diagnosis_agent(state: CopilotState, persona: str) -> dict:
    model_name = state.get("llm_model") or settings.llm_model

    # DA-33: Rule Engine deterministico — camada zero de custo.
    # Avaliada ANTES de qualquer chamada ao LLM. Combina a descricao
    # textual com a mensagem do conector (se disponivel) para maximizar
    # a cobertura de padroes. Se bater com uma regra conhecida, devolve
    # o diagnostico diretamente sem chamar o LLM (sem consumo de tokens,
    # sem latencia do modelo).
    if settings.rule_engine_enabled:
        _connector_msg = ""
        _cd = state.get("connector_data")
        if _cd and _cd.message:
            _connector_msg = f" {_cd.message}"
        _rule_text = (state.get("description") or "") + _connector_msg
        _has_connector = bool(_cd and not _cd.is_mock and not _cd.is_fallback)
        _rule_match = match_known_error(_rule_text, has_connector_data=_has_connector)
        if _rule_match:
            _rule_match = _apply_confidence_guardrails(_rule_match, state)
            return _rule_match

    prompt = _build_diagnosis_prompt(state, persona)

    if state.get("debug"):
        print("=" * 60)
        print("PROMPT ENVIADO AO LLM (ReAct v1.3):")
        print("=" * 60)
        print(prompt)
        print("=" * 60)

    # v1.3 - agente ReAct: o modelo decide autonomamente quando e
    # quantas vezes buscar na web antes de retornar o diagnostico.
    # DA-29: WEB_SEARCH_ENABLED=false nao desativava o web_search_tool
    # do agente ReAct — so desativava o web_search_NODE do grafo (passe
    # pre-retrieval). O agente ReAct e uma segunda via de busca web que
    # tambem precisa respeitar a flag. tools=[] quando desabilitado garante
    # que o LLM nao tenha o tool disponivel independente de instrucao de
    # prompt (enforcement de codigo, nao de prompt).
    web_tool = _make_web_search_tool(state)
    react_tools = [web_tool] if _web_search_allowed(state) else []
    # Instrucao adicional para forcar JSON na resposta final do agente ReAct
    json_instruction = prompts.JSON_INSTRUCTION

    def _build_and_invoke(llm):
        # Avaliacao externa (curto prazo, item 5): "structured output de
        # verdade" - response_format=DiagnosisModel faz o
        # create_react_agent (langgraph>=1.x) rodar uma chamada ADICIONAL
        # ao LLM apos o loop ReAct terminar, usando with_structured_output
        # de verdade (tool-calling nativo do provider), e devolve o
        # resultado ja validado em react_result["structured_response"] -
        # nao mais so um texto que a gente torce pra estar em JSON. O
        # o parsing por regex abaixo deixa de ser o caminho
        # principal e vira o ULTIMO fallback, so
        # usado quando structured_response nao vem preenchido.
        react_agent = create_react_agent(llm, tools=react_tools, response_format=DiagnosisModel)
        messages = {"messages": [{"role": "user", "content": prompt + json_instruction}]}
        config = {"recursion_limit": settings.react_agent_recursion_limit}
        if _langfuse_handler is not None:
            config["callbacks"] = [_langfuse_handler]
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
            react_agent_plain = create_react_agent(llm, tools=react_tools)
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
        # Regressao observada em 26/09/2026 com qwen3-coder-next via Ollama:
        # o texto final do agente trazia "matched_source": "cpi_http_401.md",
        # mas a chamada ADICIONAL de structured output devolvia o campo nulo
        # (4 de 13 casos do promptfoo, todos com diagnostico correto). Quando
        # isso acontece, recupera o valor do texto cru. Nao e uma porta para
        # alucinacao: _apply_confidence_guardrails abaixo ainda valida o nome
        # contra as fontes realmente recuperadas.
        if not diagnosis.get("matched_source"):
            recovered = _recover_matched_source_from_raw(raw)
            if recovered:
                diagnosis["matched_source"] = recovered
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
    # DA-53: proveniencia do prompt, gravada AQUI e nao no `run_diagnosis`.
    # O return antecipado do rule engine (acima) nao passa por esta linha, e
    # isso e' o correto: um diagnostico que saiu sem chamar o LLM nao foi
    # produzido por prompt nenhum. Registrar o digest nesse caminho seria
    # afirmar uma origem falsa -- e foi exatamente por isso que a coluna
    # `incidents.prompt_digest` e' anulavel em vez de ter default.
    diagnosis.update(prompts.get_spec().provenance())
    return diagnosis


def _recover_matched_source_from_raw(raw: str) -> str | None:
    """Extrai "matched_source" do texto cru da resposta do agente.

    Usado quando o structured output vem com matched_source nulo mas o
    modelo escreveu o nome do documento no texto. Devolve None para
    ausencia, null literal ou string vazia."""
    match = re_module.search(r'"matched_source"\s*:\s*"([^"]+)"', raw or "")
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


@observe_span(name="sap_specialist")
def sap_diagnosis_node(state: CopilotState) -> CopilotState:
    """Sub-agente especialista SAP - roteado pelo supervisor quando
    `agent_domain == "sap"` (ver app/agent/supervisor.py)."""
    return {"diagnosis": _run_diagnosis_agent(state, _SAP_SPECIALIST_PERSONA)}


@observe_span(name="saas_specialist")
def saas_diagnosis_node(state: CopilotState) -> CopilotState:
    """Sub-agente especialista multi-fornecedor (SaaS empresarial) -
    roteado pelo supervisor quando `agent_domain == "saas"`."""
    return {"diagnosis": _run_diagnosis_agent(state, _ENTERPRISE_SPECIALIST_PERSONA)}


@observe_span(name="generic_specialist")
def generic_diagnosis_node(state: CopilotState) -> CopilotState:
    """Sub-agente generalista de integracao - roteado pelo supervisor
    quando `agent_domain == "generic"` (nenhum dominio identificado).
    DA-22: usa persona agnosta de fornecedor em vez de reutilizar a
    persona SaaS, que assumia vocabulario de fornecedores especificos."""
    return {"diagnosis": _run_diagnosis_agent(state, _GENERIC_INTEGRATION_PERSONA)}


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
    - model_confidence: confianca auto-relatada pelo LLM pos-guardrail (P1.5)
    - diagnosis_confidence: confianca calculada pelo pipeline (P1.5)
    - has_matched_source: 1 se encontrou documento, 0 se nao (proxy de hallucination)
    - rerank_top_score: score do reranker no top resultado (qualidade do retrieval)
    - web_search_used: 1 se a busca web foi ativada nesta execucao

    Sem as duas chaves do Langfuse nao faz NADA: get_client() criaria um
    client sem chave e o SDK v4 logaria "initialized without public_key" +
    "No active span" a cada diagnostico. Tratar isso como no-op e o
    comportamento correto - metricas de observabilidade nao podem custar
    ruido em quem nao pediu observabilidade.
    """
    if not settings.langfuse_configured:
        return
    try:
        client = get_client()
        confidence = float(diagnosis.get("model_confidence", diagnosis.get("confidence", 0.0)))
        diagnosis_confidence = float(diagnosis.get("diagnosis_confidence", 0.0))
        has_source = 1.0 if diagnosis.get("matched_source") else 0.0
        web_used = (
            1.0
            if state.get("web_search_results")
            and state["web_search_results"]
            and state["web_search_results"][0].get("source") == "web_search"
            else 0.0
        )

        top_hit = state.get("retrieved_context", [{}])[0] if state.get("retrieved_context") else {}
        rerank_score = float(
            top_hit.get("rerank_score_calibrated", top_hit.get("score", 0.0))
        )  # DA-42: calibrated

        client.score_current_trace(name="model_confidence", value=confidence)
        client.score_current_trace(name="diagnosis_confidence", value=diagnosis_confidence)
        client.score_current_trace(name="has_matched_source", value=has_source)
        client.score_current_trace(name="rerank_top_score", value=rerank_score)
        client.score_current_trace(name="web_search_used", value=web_used)
    except Exception:
        logging.getLogger(__name__).debug("Langfuse metrics error", exc_info=True)


@observe_span(name="report")
def report_node(state: CopilotState) -> CopilotState:
    """P1.5 + P1.6 (revisao arquitetural externa, 23/09/2026):
    - P1.5: exibe model_confidence e diagnosis_confidence separados no report.
    - P1.6: Evidence Bundle — classifica evidencias em Primary Evidence
      (fontes de alta confianca: system_observed) e Supporting Facts
      (retrieved_document, web_untrusted, user_reported, simulated),
      em vez de uma lista plana. Deixa claro para o analista o que o
      pipeline observou diretamente versus o que veio de inferencia/RAG.
    """
    diagnosis = state.get("diagnosis", {})
    _record_quality_metrics(state, diagnosis)

    next_steps_md = "\n".join(f"- {step}" for step in diagnosis.get("next_steps", []))
    matched = diagnosis.get("matched_source") or "nenhum documento especifico identificado"

    connector_line = ""
    data = state.get("connector_data")
    if data and data.is_fallback:
        connector_line = (
            f"\n**Dados do sistema ({data.source_system}):** "
            f"identificador nao reconhecido - nenhum dado do sistema disponivel\n"
        )
    elif data:
        connector_line = (
            f"\n**Dados do sistema ({data.source_system}"
            f"{' - simulado' if data.is_mock else ''}):** "
            f"status={data.status}, codigo={data.error_code}\n"
        )

    # P1.5: exibe ambas as metricas de confianca para o analista
    model_conf = diagnosis.get("model_confidence", diagnosis.get("confidence", 0.0))
    diag_conf = diagnosis.get("diagnosis_confidence", 0.0)
    evidence_str = diagnosis.get("evidence_strength", 0.0)

    # P1.6: Evidence Bundle — classifica evidencias por confianca
    # Primary Evidence: sistema observou diretamente (conector real, rule engine)
    # Supporting Facts: inferencia/RAG/web/usuario (revisao humana recomendada)
    evidence_items = _assemble_evidence(state)
    primary = [e for e in evidence_items if e["trust_level"] == "system_observed"]
    supporting = [e for e in evidence_items if e["trust_level"] != "system_observed"]

    def _evidence_line(item: dict) -> str:
        locator_suffix = f" `{item['locator']}`" if item.get("locator") else ""
        score_suffix = ""
        if item.get("rerank_score") is not None:
            score_suffix = f" (rerank={item['rerank_score']:.3f})"
        elif item.get("retrieval_score") is not None:
            score_suffix = f" (score={item['retrieval_score']:.3f})"
        return f"- **[{item['trust_level']}]** {item['source_type']}{locator_suffix}{score_suffix}"

    primary_md = (
        "\n".join(_evidence_line(e) for e in primary)
        if primary
        else "- (nenhuma evidencia direta do sistema — diagnostico baseado em inferencia)"
    )
    supporting_md = (
        "\n".join(_evidence_line(e) for e in supporting)
        if supporting
        else "- (nenhum fato de suporte)"
    )

    report = f"""## Diagnostico do Incidente

**Descricao reportada:** {state["description"]}
{connector_line}
**Causa raiz provavel:** {diagnosis.get("probable_root_cause", "N/A")}

**Confianca:**
- `diagnosis_confidence` (pipeline): {diag_conf:.0%} — use este para automacao
- `model_confidence` (LLM pos-guardrail): {model_conf:.0%}
- `evidence_strength` (retrieval/conector): {evidence_str:.0%}
- Provider: {diagnosis.get("llm_provider_used", "N/A")} | Agente: {state.get("agent_domain", "N/A")} | Modelo: {state.get("llm_model", "N/A")} | Prompt: {diagnosis.get("prompt_version", "nenhum")}

**Documento usado como base:** {matched}

**Proximos passos:**
{next_steps_md if next_steps_md else "- (nenhum passo sugerido)"}

**Primary Evidence** (observado diretamente pelo sistema — alta confianca):
{primary_md}

**Supporting Facts** (RAG / busca web / relato do usuario — revisao humana recomendada):
{supporting_md}
"""
    return {"report_markdown": report}


def hitl_review_node(state: CopilotState) -> CopilotState:
    """Node to pause and request human review when confidence is low.

    Decision logic:
    1. Check if ONTOLOGY_ENRICHMENT_ENABLED and confidence < HITL_CONFIDENCE_THRESHOLD
    2. Build review payload from state
    3. *In production*, this would trigger an external review workflow (email, UI, API)
    4. For now, simulate review with default values (can be overridden by tests)

    Args:
        state: Current CopilotState

    Returns:
        CopilotState with hitl_request and hitl_response fields
    """
    HITL_CONFIDENCE_THRESHOLD = 0.7

    # Skip HITL if disabled or confidence is sufficient
    if not settings.ontology_enrichment_enabled:
        _logger.debug("HITL disabled, skipping review")
        return state

    if state.get("confidence") is None:
        _logger.debug("No confidence score available, skipping HITL")
        return state

    if state["confidence"] >= HITL_CONFIDENCE_THRESHOLD:
        _logger.debug("Confidence %.2f >= threshold, skipping HITL", state["confidence"])
        return state

    # Build review request
    hitl_request = {
        "incident_id": state.get("incident_id", "unknown"),
        "summary": state.get("summary", "")[:500],
        "current_confidence": state["confidence"],
        "ontology_candidates": state.get("ontology_candidates", {}),
        "diagnostic_hypothesis": state.get("diagnostic_hypothesis", ""),
    }

    # Simulate human review
    hitl_response = {
        "reviewed": True,
        "confidence_adjustment": -0.2,
        "corrected_categories": [],
        "comments": "Low confidence diagnostic, using fallback path",
        "status": "approved",
    }

    state["hitl_request"] = hitl_request
    state["hitl_response"] = hitl_response
    state["confidence_adjustment"] = hitl_response["confidence_adjustment"]

    return state


HITL_CONFIDENCE_THRESHOLD = 0.7


def risk_assessment_node(state: CopilotState) -> CopilotState:
    """Avalia risco do diagnóstico com base em HITL feedback e ontologia.

    DA-61 Fase 6: risk assessment como gate final antes de emitir relatório.
    Usa:
    - HITL review (se presente): confiança ajustada, comments, status
    - Ontology candidates: concordância entre categorias SKOS

    Limites empíricos:
    - risk_threshold = 0.7: abaixo disso, risk=high (requer humano)
    - risk_medium_threshold = 0.85: entre 0.7 e 0.85 → risk=medium

    Retorna state atualizado com:
    - risk_level: "low" | "medium" | "high"
    - risk_factors: list[str] de justificativas
    - risk_confidence: confidence ajustado (float)
    """
    risk_factors = []
    risk_confidence = state.get("confidence", 1.0)

    # Ajustar confiança com hitl_correction se disponível
    hitl_response = state.get("hitl_response")
    if hitl_response:
        adjustment = hitl_response.get("confidence_adjustment", 0.0)
        risk_confidence = max(0.0, min(1.0, risk_confidence + adjustment))
        state["risk_confidence"] = risk_confidence

        if hitl_response.get("status") == "rejected":
            risk_factors.append("HITL review rejected diagnostic")
        if hitl_response.get("corrected_categories"):
            risk_factors.append("HITL corrected categories")

    # Ontology candidates como indicador adicional
    ontology_candidates = state.get("ontology_candidates", {})
    if ontology_candidates:
        candidate_count = len(ontology_candidates)
        if candidate_count > 1:
            risk_factors.append(f"{candidate_count} ontology candidates present")
        elif candidate_count == 1:
            risk_factors.append("Single ontology candidate, low ambiguity")

    # Avaliar risco baseado na confiança ajustada
    if risk_confidence >= HITL_CONFIDENCE_THRESHOLD + 0.15:
        state["risk_level"] = "low"
    elif risk_confidence >= HITL_CONFIDENCE_THRESHOLD:
        state["risk_level"] = "medium"
    else:
        state["risk_level"] = "high"

    state["risk_factors"] = risk_factors

    _logger.debug(
        "risk_assessment_node: risk_level=%s, confidence=%.2f, factors=%s",
        state["risk_level"],
        risk_confidence,
        risk_factors,
    )

    return state
