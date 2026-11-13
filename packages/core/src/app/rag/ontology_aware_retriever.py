"""Retriever com suporte a ontology inference via SPARQL.

Fase 4: hybrid RAG + ontology enrichment. Consulta Qdrant (hybrid dense+BM25)
e enriquece o resultado com inferência semântica da ontologia SKOS (TTL).

A ontologia NAO substitui o RAG, apenas filtra/re-ranking candidatos com base
em upper categories ou next steps definidos no TTL.

DA-61 Fase 4: ontology-aware retrieval (hybrid RAG + SPARQL).
"""

import logging

from app.config import settings
from app.ontology.enrichment import get_upper_categories_from_category
from app.rag.retriever import COLLECTIONS, _retrieve_hybrid

_logger = logging.getLogger(__name__)

ONTOLOGY_UPPER_CATEGORY_THRESHOLD = 0.75


def _apply_ontology_filter(candidates: list[dict], category: str) -> tuple[list[dict], list[str]]:
    """Apply ontology-based filtering/re-ranking to candidates.

    Args:
        candidates: list of retrieved documents from hybrid RAG
        category: error category from rule_engine or diagnosis

    Returns:
        tuple: (filtered_candidates, upper_categories_used)
    """
    if not category:
        return candidates, []

    try:
        upper_cats = get_upper_categories_from_category(category)
    except FileNotFoundError:
        _logger.debug(
            "ontology_filter: failed to load upper categories for %s, using raw candidates",
            category,
        )
        return candidates, []

    if not upper_cats:
        _logger.debug("ontology_filter: no upper categories for %s", category)
        return candidates, []

    # Filter candidates that match any upper category in metadata
    matched = []
    matched_cats = set()

    for doc in candidates:
        doc_cats = doc.get("metadata", {}).get("upper_categories", [])
        for cat in doc_cats:
            if cat in upper_cats:
                matched.append(doc)
                matched_cats.add(cat)
                break

    if matched:
        # Re-rank: exact category match first, then upper category matches
        category_lower = category.lower()
        matched.sort(
            key=lambda d: category_lower not in d.get("metadata", {}).get("categories", [])
        )
        _logger.debug(
            "ontology_filter: %d/%d candidates matched upper cats %s",
            len(matched),
            len(candidates),
            upper_cats,
        )
        return matched[:3], list(matched_cats)

    _logger.debug("ontology_filter: no candidates matched upper cats %s", upper_cats)
    return candidates, []


def retrieve_hybrid_ontology_aware(
    query: str,
    target: str = "incidents",
    top_k: int = 5,
    score_threshold: float = 0.5,
    ontology_category: str | None = None,
) -> list[dict]:
    """Hybrid RAG + ontology inference retrieval.

    Args:
        query: user query
        target: collection to query (default: "incidents")
        top_k: max documents to return
        score_threshold: minimum similarity score
        ontology_category: error category for ontology enrichment

    Returns:
        List of documents, optionally filtered/re-ranked by ontology
    """
    if target not in COLLECTIONS:
        raise ValueError(f"Unknown target: {target}. Available: {list(COLLECTIONS.keys())}")

    # Step 1: hybrid retrieval (dense + sparse)
    candidates = _retrieve_hybrid(query, COLLECTIONS[target], top_k * 2)

    if not candidates:
        return []

    # Step 2: apply ontology enrichment if category provided
    if ontology_category and settings.ontology_enrichment_enabled:
        candidates, _ = _apply_ontology_filter(candidates, ontology_category)

    # Step 3: truncate to top_k (already re-ranked if ontology applied)
    return candidates[:top_k]
