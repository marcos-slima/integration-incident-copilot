"""Neo4j loader for SKOS taxonomy importation (DA-62)."""

from __future__ import annotations

import logging
from typing import Any

from neo4j import (
    AsyncDriver,
    AsyncGraphDatabase,
)
from neo4j import (
    exceptions as neo4j_exceptions,
)

logger = logging.getLogger(__name__)

# Constants for Neo4j configuration — never hardcoded credentials
NEO4J_URI_VAR = "NEO4J_URI"
NEO4J_USER_VAR = "NEO4J_USER"
NEO4J_PASSWORD_VAR = "NEO4J_PASSWORD"


async def load_error_rules_to_neo4j(neo4j_uri: str, username: str, password: str) -> int:
    """Import SKOS error codes from TTL to Neo4j in an idempotent way.

    Returns the number of concepts imported.
    Calls should be idempotent: re-running does not create duplicates.
    """
    from app.agent.ontology_loader import load_error_rules

    rules = load_error_rules()
    if not rules:
        return 0

    driver: AsyncDriver | None = None
    try:
        driver = AsyncGraphDatabase.driver(
            neo4j_uri, auth=(username, password), connection_timeout=30
        )

        async def _import():
            async with driver.session() as session:
                count = 0
                for rule in rules:
                    await session.execute_write(
                        _upsert_concept,
                        {
                            "uri": rule.category,
                            "label": rule.patterns[0] if rule.patterns else "",
                            "confidence": rule.confidence,
                            "root_cause": rule.probable_root_cause,
                            "next_steps": rule.next_steps or [],
                            "broader": [],
                        },
                    )
                    count += 1
                return count

        return await _import()

    except neo4j_exceptions.ServiceUnavailable as exc:
        logger.warning("Neo4j service unavailable: %s", exc)
        return 0
    except neo4j_exceptions.AuthError as exc:
        logger.error("Neo4j authentication failed: %s", exc)
        return 0
    finally:
        if driver:
            await driver.close()


async def _upsert_concept(
    tx: Any,
    data: dict[str, Any],
) -> None:
    """Upsert a concept node with SKOS properties.

    Creates/updates:
    - Concept node (label :ErrorConcept) with properties
    - Hierarchy edges via skos:broader to existing concepts
    """
    query = """
    MERGE (c:ErrorConcept {uri: $uri})
    ON CREATE SET c.label = $label,
                  c.confidence = $confidence,
                  c.root_cause = $root_cause,
                  c.next_steps = $next_steps,
                  c.created_at = timestamp()
    ON MATCH SET c.label = $label,
                 c.confidence = $confidence,
                 c.root_cause = $root_cause,
                 c.next_steps = $next_steps,
                 c.updated_at = timestamp()

    WITH c, CASE WHEN $broader IS NOT NULL AND size($broader) > 0 THEN $broader ELSE [] END AS broader_list
    UNWIND broader_list AS broader_uri
    OPTIONAL MATCH (b:ErrorConcept {uri: broader_uri})
    WHERE b IS NOT NULL
    MERGE (c)-[:skos_broader]->(b)
    """

    await tx.run(query, **data)


async def get_incidents_by_error_uri(
    neo4j_uri: str, username: str, password: str, uri: str
) -> list[dict[str, Any]]:
    """Retrieve incidents linked to a specific error concept via Neo4j."""
    driver: AsyncDriver | None = None
    try:
        driver = AsyncGraphDatabase.driver(
            neo4j_uri, auth=(username, password), connection_timeout=30
        )

        async def _query():
            async with driver.session() as session:
                result = await session.execute_read(_match_incidents_by_error, {"uri": uri})
                return [dict(record) for record in result]

        return await _query()

    except neo4j_exceptions.ServiceUnavailable as exc:
        logger.warning("Neo4j service unavailable: %s", exc)
        return []
    except neo4j_exceptions.AuthError as exc:
        logger.error("Neo4j authentication failed: %s", exc)
        return []
    finally:
        if driver:
            await driver.close()


async def _match_incidents_by_error(
    tx: Any,
    params: dict[str, str],
) -> list[Any]:
    """Match incidents connected to an error concept."""
    query = """
    MATCH (e:ErrorConcept {uri: $uri})<-[*1..3]-(i:Incident)
    WHERE i.status <> 'resolved'
    RETURN DISTINCT i.id AS incident_id,
           i.title AS incident_title,
           i.connector_source_system AS source_system,
           i.created_at AS incident_date
    ORDER BY i.created_at DESC
    LIMIT 20
    """
    result = await tx.run(query, **params)
    return await result.fetch()


async def get_error_hierarchy(
    neo4j_uri: str, username: str, password: str, uri: str
) -> list[dict[str, Any]]:
    """Retrieve the SKOS hierarchy (broader/narrower) for a given error concept."""
    driver: AsyncDriver | None = None
    try:
        driver = AsyncGraphDatabase.driver(
            neo4j_uri, auth=(username, password), connection_timeout=30
        )

        async def _query():
            async with driver.session() as session:
                result = await session.execute_read(_query_hierarchy, {"uri": uri})
                return [dict(record) for record in result]

        return await _query()

    except neo4j_exceptions.ServiceUnavailable as exc:
        logger.warning("Neo4j service unavailable: %s", exc)
        return []
    except neo4j_exceptions.AuthError as exc:
        logger.error("Neo4j authentication failed: %s", exc)
        return []
    finally:
        if driver:
            await driver.close()


async def _query_hierarchy(
    tx: Any,
    params: dict[str, str],
) -> list[Any]:
    """Query SKOS broader/narrower relations."""
    query = """
    MATCH (c:ErrorConcept {uri: $uri})
    OPTIONAL MATCH (c)-[:skos_broader]->(b:ErrorConcept)
    OPTIONAL MATCH (narrower)-[:skos_broader]->(c)
    RETURN c.uri AS root_uri,
           collect(DISTINCT b.uri) AS broader_uris,
           collect(DISTINCT narrower.uri) AS narrower_uris
    """
    result = await tx.run(query, **params)
    record = await result.single()
    if not record:
        return []
    return [
        {
            "root_uri": record["root_uri"],
            "broader": record["broader_uris"] or [],
            "narrower": record["narrower_uris"] or [],
        }
    ]
