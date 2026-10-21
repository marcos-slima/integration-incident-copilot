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
    # Normalize o nome da categoria de entrada para casar com os labels
    normalized_input = category.lower().replace(" ", "_")
    category_to_iri = {}
    for s, _, label in graph.triples((None, SKOS.prefLabel, None)):
        if isinstance(label, str):
            label_category = label.lower().replace(" ", "_")
            category_to_iri[label_category] = s

    if normalized_input not in category_to_iri:
        return []

    # Query upper categories via skos:broader*
    query = (
        """
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
    PREFIX ex: <http://example.org/iic/error_codes#>
    SELECT ?upper WHERE {
      BIND(<"""
        + category_to_iri[normalized_input]
        + """> AS ?concept)
      ?concept skos:broader* ?upper .
      FILTER(?upper != ?concept)
    }
    """
    )

    results = list(graph.query(query))
    # Use str() em vez de n3() para evitar '< >' no output
    return [str(row["upper"]) for row in results]


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

    # Build category → IRI mapping (normalizar tambem aqui)
    normalized_input = category.lower().replace(" ", "_")
    category_to_iri = {}
    for s, _, label in graph.triples((None, SKOS.prefLabel, None)):
        if isinstance(label, str):
            label_category = label.lower().replace(" ", "_")
            category_to_iri[label_category] = s

    if normalized_input not in category_to_iri:
        return []

    # Query next_steps property
    query = (
        """
    PREFIX ex: <http://example.org/iic/error_codes#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?step WHERE {
      BIND(<"""
        + category_to_iri[normalized_input]
        + """> AS ?concept)
      ?concept ex:next_steps ?step .
    }
    """
    )

    results = list(graph.query(query))
    # Converte para string se for URIRef
    return [str(row["step"]) for row in results]


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

    # DA-61 Fase 2b: Implement consistency checks
    # Check 1: Verify rule_engine_category matches error category
    incident_cat = result[0]["incident_category"]
    error_cat = result[0]["error_category"]

    if incident_cat and error_cat and incident_cat.lower() != error_cat.lower():
        issues.append(f"Category mismatch: incident has '{incident_cat}', error has '{error_cat}'")
        recommendations.append("Verify the rule engine category matches the error category")

    # Check 2: Verify upper categories are consistent
    upper_cats = result[0]["upper_categories"] or []
    if upper_cats and error_cat in upper_cats:
        issues.append("Circular reference detected: error category in upper categories")
        recommendations.append("Check SKOS hierarchy for circular broader relationships")

    # Check 3: Verify description has relevant keywords
    description = result[0]["description"] or ""
    if description:
        # Simple keyword check based on category
        keywords = {
            "oauth": ["token", "oauth", "authentication"],
            "http": ["status", "code", "response"],
            "rfc": ["function", "calling", "rfc"],
        }
        cat_key = error_cat.lower().split("_")[0] if error_cat else ""
        if cat_key in keywords:
            found = any(kw in description.lower() for kw in keywords[cat_key])
            if not found:
                recommendations.append(
                    f"Consider adding keywords for {cat_key} to improve matching"
                )

    return {"consistent": len(issues) == 0, "issues": issues, "recommendations": recommendations}
