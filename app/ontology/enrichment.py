# app/ontology/enrichment.py
"""Fase 2: Neo4j + SKOS integration for incident enrichment."""

from neo4j import Driver
from rdflib import Graph
from rdflib.namespace import SKOS

from app.config import settings


def get_upper_categories_from_category(category: str, ttl_path: str | None = None) -> list[str]:
    """Infer upper categories from TTL SKOS hierarchy.

    Args:
        category: error category (e.g., "http_timeout")
        ttl_path: path to error_codes.ttl (default: app/ontology/error_codes.ttl)

    Returns:
        List of upper category IRIs (e.g., ["http://example.org/iic/error_codes#NetworkError"])

    Example:
        >>> get_upper_categories_from_category("http_timeout")
        ["http://example.org/iic/error_codes#NetworkError"]
    """
    if ttl_path is None:
        ttl_path = settings.ontology_ttl_path

    graph = Graph()
    graph.parse(ttl_path, format="turtle")

    # Build category → IRI mapping
    category_to_iri = {}
    for s, _, label in graph.triples((None, SKOS.prefLabel, None)):
        if isinstance(label, str):
            category = label.lower().replace(" ", "_")
            category_to_iri[category] = s

    if category not in category_to_iri:
        return []

    # Query upper categories via skos:broader*
    query = (
        """
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
    PREFIX ex: <http://example.org/iic/error_codes#>
    SELECT ?upper WHERE {
      BIND(<"""
        + category_to_iri[category]
        + """> AS ?concept)
      ?concept skos:broader* ?upper .
      FILTER(?upper != ?concept)
    }
    """
    )

    results = list(graph.query(query))
    return [row["upper"].n3() for row in results]


def enrich_incident_with_ontology(incident_id: str, neo4j_driver: Driver) -> None:
    """Link incident to ontology concepts and infer upper categories.

    Args:
        incident_id: incident ID
        neo4j_driver: Neo4j driver instance

    Example:
        >>> driver = neo4j.GraphDatabase.driver("bolt://localhost:7687")
        >>> enrich_incident_with_ontology("INC-123", driver)
    """
    query = """
    MATCH (i:Incident {id: $incident_id})
    WITH i, i.diagnosis->>'rule_engine_category' AS category
    WHERE category IS NOT NULL
    MATCH (e:ErrorCategory {category: category})
    CREATE (i)-[:HAS_ERROR]->(e)
    WITH e
    CALL apoc.cypher.doIt("
        MATCH (e)<-[:SKOS_BROADER*]-(upper:ErrorCategory)
        RETURN collect(upper.category) AS upper_categories
    ", {e: e}) YIELD value
    WITH e, value.upper_categories AS upper_categories
    UNWIND upper_categories AS upper
    MATCH (upper_node:ErrorCategory {category: upper})
    CREATE (i)-[:HAS_UPPER_LEVEL_ERROR]->(upper_node)
    """
    neo4j_driver.execute(query, {"incident_id": incident_id})


def get_next_steps_from_category(category: str, ttl_path: str | None = None) -> list[str]:
    """Extract recommended next steps from TTL.

    Args:
        category: error category (e.g., "oauth_token_expired")
        ttl_path: path to error_codes.ttl

    Returns:
        List of next steps (e.g., ["refresh_token", "reauth"])

    Example:
        >>> get_next_steps_from_category("oauth_token_expired")
        ["Refresh token", "Reauth"]
    """
    if ttl_path is None:
        ttl_path = settings.ontology_ttl_path

    graph = Graph()
    graph.parse(ttl_path, format="turtle")

    # Build category → IRI mapping
    category_to_iri = {}
    for s, _, label in graph.triples((None, SKOS.prefLabel, None)):
        if isinstance(label, str):
            category = label.lower().replace(" ", "_")
            category_to_iri[category] = s

    if category not in category_to_iri:
        return []

    # Query next_steps property
    query = (
        """
    PREFIX ex: <http://example.org/iic/error_codes#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?step WHERE {
      BIND(<"""
        + category_to_iri[category]
        + """> AS ?concept)
      ?concept ex:next_steps ?step .
    }
    """
    )

    results = list(graph.query(query))
    return [row["step"] for row in results]


def infer_consistency(incident_id: str, neo4j_driver: Driver) -> dict:
    """Infer consistency between incident and ontology.

    Args:
        incident_id: incident ID
        neo4j_driver: Neo4j driver instance

    Returns:
        Dict with consistency check results

    Example:
        >>> infer_consistency("INC-123", driver)
        {"consistent": True, "issues": [], "recommendations": []}
    """
    query = """
    MATCH (i:Incident {id: $incident_id})-[:HAS_ERROR]->(e:ErrorCategory)
    WITH i, e
    OPTIONAL MATCH (i)-[:HAS_UPPER_LEVEL_ERROR]->(upper:ErrorCategory)
    RETURN i.diagnosis->>'rule_engine_category' AS incident_category,
           e.category AS error_category,
           collect(upper.category) AS upper_categories,
           i.description AS description
    """

    result = neo4j_driver.execute(query, {"incident_id": incident_id})
    if not result:
        return {"consistent": False, "issues": ["No error category found"], "recommendations": []}

    # Perform consistency check
    issues = []
    recommendations = []

    # TODO: Implement consistency checks (e.g., confidence thresholds, category alignment)

    return {"consistent": len(issues) == 0, "issues": issues, "recommendations": recommendations}
