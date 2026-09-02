"""GraphRAG - enriquecimento do diagnostico com historico relacional
(Neo4j), reservado para uso futuro (ver docs/ARCHITECTURE.md).

Desligado por default (`GRAPH_RAG_ENABLED=false`). O motivo de existir
desligado por default: Qdrant (busca semantica em texto) ja resolve o
caso de uso principal (achar o documento de conhecimento certo); o
grafo so agrega valor quando ha HISTORICO acumulado de incidentes reais
(nao os 4-8 documentos de demonstracao deste repositorio) - relacionar
"essa RFC destination ja teve N incidentes antes, com essas causas" so
faz sentido depois de meses de uso real, nao no dia 1.

O que este modulo faz quando ligado:
  1. `upsert_incident_graph(...)` - depois de cada diagnostico, grava
     no Neo4j os nos (Incident, Interface, System, Document) e as
     relacoes entre eles
  2. `graph_context(...)` - antes de gerar o diagnostico, consulta o
     grafo por incidentes ANTERIORES na mesma interface/sistema, para
     dar ao LLM contexto de recorrencia ("isso ja aconteceu 3x, sempre
     pela mesma causa") que a busca vetorial sozinha nao da (Qdrant
     acha o documento de CONHECIMENTO mais parecido, nao o HISTORICO
     relacional de uma interface especifica)

Como ativar de verdade (nao ha nada para descomentar no Python - so
infraestrutura + uma flag):
  1. `docker compose --profile graphrag up -d neo4j`
  2. `GRAPH_RAG_ENABLED=true` no `.env` (+ NEO4J_PASSWORD se voce
     mudou o default do docker-compose)
  3. Rodar `uv run python -m app.rag.graph_store --init` uma vez, para
     criar as constraints/indices

Testado com um driver Neo4j FAKE (`tests/test_graph_store.py`) que
verifica as queries Cypher e o mapeamento de dados - NAO testado
contra um Neo4j real (ver ressalva equivalente em
`RFCConnector._fetch_real`), porque a decisao de negocio foi manter
Neo4j fora do `docker compose up` default (ver Decisao de Arquitetura
#9 no README) - subir e derrubar so para este teste teria custo maior
que o beneficio nesta fase.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

from app.config import settings


class _Neo4jSession(Protocol):
    """Subconjunto do protocolo `neo4j.Session` que este modulo usa -
    permite injetar um driver/sessao fake nos testes sem precisar de
    um Neo4j real (mesmo espirito de `httpx.MockTransport` nos
    conectores HTTP)."""

    def run(self, query: str, **parameters: Any) -> Any: ...


@dataclass
class RelatedIncident:
    interface_identifier: str
    source_system: str
    root_cause: str
    matched_document: str | None


def is_enabled() -> bool:
    return settings.graph_rag_enabled


@lru_cache(maxsize=1)
def _get_driver():
    """Import tardio de `neo4j` - so acontece se GraphRAG estiver
    habilitado, para nao exigir um Neo4j vivo em nenhum caminho
    default (mock/demo) do projeto."""
    from neo4j import GraphDatabase

    return GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )


def _get_session(session: _Neo4jSession | None = None) -> _Neo4jSession:
    if session is not None:
        return session
    return _get_driver().session()


_CONSTRAINTS = [
    "CREATE CONSTRAINT incident_id IF NOT EXISTS FOR (i:Incident) REQUIRE i.id IS UNIQUE",
    (
        "CREATE CONSTRAINT interface_id IF NOT EXISTS "
        "FOR (f:Interface) REQUIRE (f.type, f.identifier) IS UNIQUE"
    ),
    "CREATE CONSTRAINT system_name IF NOT EXISTS FOR (s:System) REQUIRE s.name IS UNIQUE",
]


def ensure_constraints(session: _Neo4jSession | None = None) -> None:
    sess = _get_session(session)
    for statement in _CONSTRAINTS:
        sess.run(statement)


_UPSERT_QUERY = """
MERGE (i:Incident {id: $incident_id})
  SET i.description = $description, i.root_cause = $root_cause,
      i.confidence = $confidence, i.created_at = datetime()
MERGE (f:Interface {type: $interface_type, identifier: $identifier})
MERGE (i)-[:AFFECTS]->(f)
MERGE (s:System {name: $source_system})
MERGE (f)-[:RUNS_ON]->(s)
WITH i
FOREACH (_ IN CASE WHEN $matched_document IS NOT NULL THEN [1] ELSE [] END |
  MERGE (d:Document {source: $matched_document})
  MERGE (i)-[:HAS_ROOT_CAUSE_IN]->(d)
)
"""


def upsert_incident_graph(
    incident_id: str,
    description: str,
    interface_type: str | None,
    identifier: str | None,
    source_system: str | None,
    root_cause: str,
    confidence: float,
    matched_document: str | None,
    session: _Neo4jSession | None = None,
) -> None:
    """Grava um diagnostico concluido no grafo. No-op silencioso se
    nao houver interface/identificador (incidente so-texto, sem
    conector) - nao ha o que relacionar nesse caso."""
    if not interface_type or not identifier:
        return
    sess = _get_session(session)
    sess.run(
        _UPSERT_QUERY,
        incident_id=incident_id,
        description=description,
        interface_type=interface_type,
        identifier=identifier,
        source_system=source_system or interface_type,
        root_cause=root_cause,
        confidence=confidence,
        matched_document=matched_document,
    )


_RELATED_QUERY = """
MATCH (f:Interface {type: $interface_type, identifier: $identifier})<-[:AFFECTS]-(i:Incident)
OPTIONAL MATCH (i)-[:HAS_ROOT_CAUSE_IN]->(d:Document)
OPTIONAL MATCH (f)-[:RUNS_ON]->(s:System)
RETURN i.root_cause AS root_cause, s.name AS source_system, d.source AS matched_document
ORDER BY i.created_at DESC
LIMIT $limit
"""


def graph_context(
    interface_type: str | None,
    identifier: str | None,
    limit: int = 5,
    session: _Neo4jSession | None = None,
) -> list[RelatedIncident]:
    """Retorna incidentes anteriores conhecidos na mesma interface,
    mais recentes primeiro. Lista vazia se GraphRAG estiver desligado,
    sem historico, ou sem interface/identificador informado - sempre
    seguro de chamar incondicionalmente do grafo LangGraph."""
    if not is_enabled() or not interface_type or not identifier:
        return []

    sess = _get_session(session)
    result = sess.run(
        _RELATED_QUERY, interface_type=interface_type, identifier=identifier, limit=limit
    )
    return [
        RelatedIncident(
            interface_identifier=identifier,
            source_system=record["source_system"] or interface_type,
            root_cause=record["root_cause"] or "",
            matched_document=record["matched_document"],
        )
        for record in result
    ]


def format_graph_context_for_prompt(related: list[RelatedIncident]) -> str:
    """Formata o historico relacional como bloco de texto pronto para
    entrar no prompt de diagnostico - separado da consulta em si para
    poder ser testado sem depender de nenhum driver."""
    if not related:
        return ""
    lines = [
        f"- causa raiz anterior: {r.root_cause} (documento: {r.matched_document or 'N/A'})"
        for r in related
    ]
    return (
        f"\nHistorico conhecido desta interface ({len(related)} incidente(s) anterior(es), "
        f"mais recente primeiro):\n" + "\n".join(lines) + "\n"
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Utilitarios de manutencao do GraphRAG (Neo4j)")
    parser.add_argument(
        "--init", action="store_true", help="Cria as constraints/indices necessarias"
    )
    args = parser.parse_args()

    if not is_enabled():
        print(
            "GRAPH_RAG_ENABLED=false - nada a fazer. Ative no .env e suba o "
            "Neo4j (docker compose --profile graphrag up -d neo4j) primeiro."
        )
    elif args.init:
        ensure_constraints()
        print("Constraints/indices do GraphRAG criados/confirmados no Neo4j.")
    else:
        parser.print_help()
