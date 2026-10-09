"""Ontology agent node para LangGraph - DA-61 Fase 5.

Enriquece diagnostico com candidatos recuperados via hybrid RAG + ontology-aware filtering.
"""

import logging

from app.agent.state import CopilotState
from app.config import settings
from app.rag.ontology_aware_retriever import retrieve_hybrid_ontology_aware

_logger = logging.getLogger(__name__)


def ontology_agent_node(state: CopilotState) -> CopilotState:
    """Recupera candidatos com inferencia semantica da ontologia SKOS.

    Quando ONTOLOGY_ENRICHMENT_ENABLED=true e ontology_category esta
    presente, consulta a ontologia (error_codes.ttl) para obter upper
    categories e filtra/re-ranking candidatos do RAG hybrido (Qdrant).
    Quando ontology enrichment esta desligado, devolve dicionario vazio.

    Retorno: {"ontology_candidates": {...}} quando ontology enrichment
    esta habilitado e ha uma categoria disponivel.

    DA-61 Fase 5: ontology agent node para enriquecimento de candidatos
    (vs. ontology_enrich_node da Fase 2/3 que so fornece upper categories
    como contexto para o prompt do LLM).
    """
    # Check if ontology enrichment is enabled
    if not settings.ontology_enrichment_enabled:
        _logger.debug("ontology_agent_node: ontology enrichment desligado, retornando lista vazia")
        return {"ontology_candidates": {}}

    # Extract ontology category from state (can come from ontology_enrich or diagnosis)
    category = state.get("ontology_category") or state.get("diagnosis", {}).get(
        "rule_engine_category"
    )

    # If no category available, return empty dict
    if not category:
        _logger.debug("ontology_agent_node: ontology_category ausente, retornando lista vazia")
        return {"ontology_candidates": {}}

    try:
        # Execute ontology-aware retrieval (which already does hybrid + ontology filtering)
        candidates = retrieve_hybrid_ontology_aware(
            query=state.get("description", ""),
            top_k=5,
            ontology_category=category,
        )

        # Convert candidates to dictionary format for LangGraph state
        candidates_dict = {}
        for idx, doc in enumerate(candidates):
            candidates_dict[f"candidate_{idx}"] = {
                "source": doc.get("source", ""),
                "text": doc.get("text", ""),
                "score": doc.get("score", 0.0),
                "metadata": doc.get("metadata", {}),
            }

        _logger.debug(
            "ontology_agent_node: recuperados %d candidates com ontology para categoria %s",
            len(candidates),
            category,
        )

        return {"ontology_candidates": candidates_dict}
    except FileNotFoundError as exc:
        _logger.warning(
            "ontology_agent_node: error_codes.ttl not found for category %s: %s",
            category,
            exc,
        )
        return {"ontology_candidates": {}}
