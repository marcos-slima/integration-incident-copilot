"""Grafo LangGraph do SAP Integration Copilot.

Fluxo:
    connector -> retrieve -> [graph_enrich] -> web_search
    -> diagnose -> [graph_write] -> report

Ver docs/ARCHITECTURE.md para detalhamento por camada.
"""

import os

from langfuse import get_client, observe

from app.agent.nodes import (
    connector_node,
    diagnose_node,
    graph_enrich_node,
    graph_write_node,
    report_node,
    retrieve_node,
    web_search_node,
)
from app.agent.state import CopilotState
from app.config import settings
from app.models import DiagnosisResponse, IncidentRequest

os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)
os.environ.setdefault("LANGFUSE_BASE_URL", settings.langfuse_host)

from langgraph.graph import END, StateGraph


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
        evidence_strength=diagnosis.get("evidence_strength"),
        llm_provider_used=diagnosis.get("llm_provider_used"),
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
