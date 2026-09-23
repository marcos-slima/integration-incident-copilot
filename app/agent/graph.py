"""Grafo LangGraph do SAP Integration Copilot.

Fluxo (DA-22 - multi-agente, supervisor + especialistas):
    supervisor -> connector -> retrieve -> [graph_enrich] -> {sap_diagnose | saas_diagnose | generic_diagnose} -> [graph_write] -> report

Busca web: realizada pelo tool do agente ReAct dentro de sap_diagnose/saas_diagnose
    quando web_search_enabled=True e o LLM decide chamar (nao e um node separado no grafo).

O supervisor (app/agent/supervisor.py) roda PRIMEIRO e decide
deterministicamente qual sub-agente especialista trata o diagnostico
(SAP ou multi-fornecedor/generico) - nunca os dois no mesmo incidente.
Ver docs/ARCHITECTURE.md para detalhamento por camada.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from uuid import uuid4

from langfuse import get_client, observe

from app.agent.nodes import (
    _assemble_evidence,
    connector_node,
    generic_diagnosis_node,
    graph_enrich_node,
    graph_write_node,
    report_node,
    retrieve_node,
    saas_diagnosis_node,
    sap_diagnosis_node,
)
from app.agent.state import CopilotState
from app.agent.supervisor import supervisor_node
from app.config import settings
from app.exceptions import DiagnosisTimeoutError
from app.models import DiagnosisResponse, IncidentRequest

os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)
os.environ.setdefault("LANGFUSE_BASE_URL", settings.langfuse_host)

from langgraph.graph import END, StateGraph


def _route_to_specialist(state: CopilotState) -> str:
    """Roteamento condicional (DA-22): le `agent_domain`, ja decidido
    pelo supervisor_node no inicio do grafo, e direciona para o node
    especialista correspondente. "generic" (nenhum dominio identificado)
    cai no especialista multi-fornecedor - ver
    app/agent/supervisor.py::classify_domain para a logica completa."""
    domain = state.get("agent_domain")
    if domain == "sap":
        return "sap_diagnose"
    if domain == "generic":
        return "generic_diagnose"
    return "saas_diagnose"


def build_graph():
    """O grafo tem duas formas: linear (default) ou com enriquecimento
    de GraphRAG intercalado, dependendo de `settings.graph_rag_enabled`
    - decidido uma vez na construcao, nao a cada execucao. Com
    GraphRAG desligado (default), o grafo e IDENTICO ao de antes desta
    fase - zero custo/comportamento novo (alem do roteamento multi-
    agente DA-22, que roda sempre, com ou sem GraphRAG). Ver
    app/rag/graph_store.py para como ativar o GraphRAG de verdade."""
    graph = StateGraph(CopilotState)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("connector", connector_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("sap_diagnose", sap_diagnosis_node)
    graph.add_node("saas_diagnose", saas_diagnosis_node)
    graph.add_node("generic_diagnose", generic_diagnosis_node)
    graph.add_node("report", report_node)

    graph.set_entry_point("supervisor")
    graph.add_edge("supervisor", "connector")
    graph.add_edge("connector", "retrieve")

    if settings.graph_rag_enabled:
        graph.add_node("graph_enrich", graph_enrich_node)
        graph.add_node("graph_write", graph_write_node)
        graph.add_edge("retrieve", "graph_enrich")
        graph.add_conditional_edges(
            "graph_enrich",
            _route_to_specialist,
            {
                "sap_diagnose": "sap_diagnose",
                "saas_diagnose": "saas_diagnose",
                "generic_diagnose": "generic_diagnose",
            },
        )
        graph.add_edge("sap_diagnose", "graph_write")
        graph.add_edge("saas_diagnose", "graph_write")
        graph.add_edge("generic_diagnose", "graph_write")
        graph.add_edge("graph_write", "report")
    else:
        graph.add_conditional_edges(
            "retrieve",
            _route_to_specialist,
            {
                "sap_diagnose": "sap_diagnose",
                "saas_diagnose": "saas_diagnose",
                "generic_diagnose": "generic_diagnose",
            },
        )
        graph.add_edge("sap_diagnose", "report")
        graph.add_edge("saas_diagnose", "report")
        graph.add_edge("generic_diagnose", "report")

    graph.add_edge("report", END)

    return graph.compile()


_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


# Avaliacao externa (curto prazo, item 4): pool dedicado e pequeno (nao
# o threadpool default do FastAPI/Starlette, que ja roda o endpoint
# /diagnose sincrono em si) - so serve pra rodar get_graph().invoke()
# COM um watchdog (.result(timeout=...)) por cima, ja que o codigo
# sincrono do LangGraph nao tem nenhum ponto de cancelamento cooperativo
# (nao e async/await) para usar asyncio.wait_for diretamente. Criado uma
# vez, reaproveitado entre chamadas - abrir uma thread nova por request
# seria desperdicio.
_graph_invoke_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="diagnosis-invoke")


def _invoke_graph_with_timeout(initial_state: CopilotState) -> CopilotState:
    """Roda get_graph().invoke(initial_state) com um teto de tempo
    (settings.diagnosis_timeout_seconds) para o pipeline INTEIRO -
    retrieval + GraphRAG + 1-2 chamadas LLM do ReAct + relatorio.

    IMPORTANTE: isso e um watchdog, nao um cancelamento real - ao
    estourar o timeout, a thread que roda o grafo CONTINUA executando
    em segundo plano (o LangGraph/LangChain nao expoe um ponto de
    cancelamento cooperativo no meio de uma chamada LLM sincrona); o
    que este timeout garante e que o CALLER (a requisicao HTTP) nunca
    fica esperando mais que o teto configurado, mesmo que a etapa
    interna trave. Mesma limitacao pratica que qualquer watchdog sobre
    codigo sincrono sem pontos de cancelamento - documentada aqui em
    vez de fingida como cancelamento de verdade."""
    future = _graph_invoke_pool.submit(get_graph().invoke, initial_state)
    try:
        return future.result(timeout=settings.diagnosis_timeout_seconds)
    except FutureTimeoutError as exc:
        raise DiagnosisTimeoutError(
            f"Diagnostico excedeu o timeout de {settings.diagnosis_timeout_seconds}s "
            "(settings.diagnosis_timeout_seconds) - o pipeline de retrieval/GraphRAG/LLM "
            "nao terminou a tempo."
        ) from exc


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
    # DA-28: id gerado aqui (nao mais dentro de graph_write_node) para
    # poder ser devolvido em DiagnosisResponse.incident_id - sem isso,
    # nao havia como referenciar um incidente especifico depois pra
    # chamar verify_incident() (POST /incidents/{id}/verify).
    incident_id = str(uuid4())
    initial_state: CopilotState = {
        "description": request.description,
        "logs": request.logs,
        "payload": request.payload,
        "interface_type": request.interface_type,
        "identifier": request.identifier,
        "llm_model": llm_model or settings.llm_model,
        "debug": debug,
        "incident_id": incident_id,
    }
    final_state = _invoke_graph_with_timeout(initial_state)
    diagnosis = final_state.get("diagnosis", {})

    # Avaliacao externa (medio prazo, item 5): captura o trace_id do
    # Langfuse ENQUANTO ainda estamos dentro do span de
    # "sap_copilot_diagnosis" (este @observe, ver decorator acima) -
    # get_current_trace_id() resolve pelo contexto da execucao atual,
    # entao so funciona chamado daqui de dentro, nao depois. None se o
    # Langfuse nao estiver configurado/ativo (tracing desabilitado) -
    # graceful, mesmo padrao de qualquer outra integracao opcional
    # deste projeto.
    trace_id = get_client().get_current_trace_id()

    return DiagnosisResponse(
        probable_root_cause=diagnosis.get("probable_root_cause", "N/A"),
        model_confidence=float(diagnosis.get("model_confidence", diagnosis.get("confidence", 0.0))),
        diagnosis_confidence=float(diagnosis.get("diagnosis_confidence", 0.0)),
        next_steps=diagnosis.get("next_steps", []),
        report_markdown=final_state.get("report_markdown", ""),
        matched_source=diagnosis.get("matched_source"),
        evidence_strength=diagnosis.get("evidence_strength"),
        llm_provider_used=diagnosis.get("llm_provider_used"),
        agent_domain=final_state.get("agent_domain"),
        # DA-25: mesma montagem deterministica usada no report_markdown
        # (ver report_node) - chamada de novo aqui sobre final_state
        # (nao guardada no state) porque e uma funcao pura e barata, e
        # evita adicionar mais uma chave ao CopilotState so pra passar
        # o mesmo dado adiante.
        evidence=_assemble_evidence(final_state),
        # DA-28: so tem sentido consultar/verificar depois se o
        # GraphRAG estiver ligado E o incidente tiver sido de fato
        # gravado (graph_write_node e no-op sem interface/identifier -
        # ver upsert_incident_graph) - devolver o id de qualquer jeito
        # seria enganoso (sugeriria que da pra verificar algo que nunca
        # foi persistido).
        incident_id=incident_id
        if settings.graph_rag_enabled and request.interface_type and request.identifier
        else None,
        trace_id=trace_id,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("description", nargs="*", default=[])
    parser.add_argument(
        "--interface",
        choices=["odata", "rfc", "servicenow", "salesforce", "workday", "ariba", "cap", "apim"],
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
