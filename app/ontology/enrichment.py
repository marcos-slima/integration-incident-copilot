# app/ontology/enrichment.py
"""Fase 2: Neo4j + SKOS integration for incident enrichment.

IIC-AUD-302: a normalizacao de categorias ignora separadores (CamelCase,
espacos, `_` e `-`) dos dois lados, e as consultas usam `initBindings` em
vez de concatenar URIs na string SPARQL. Sem isso o `str + URIRef` criava
um `URIRef` invalido (aviso do rdflib) e o resultado nao dependia da
categoria de entrada.
"""

from __future__ import annotations

import re
from typing import Any, cast

from rdflib import Graph, URIRef
from rdflib.namespace import SKOS

from app.config import settings

_NEXT_STEPS_PREFIX = "next_steps:"


def _normalize(category: str) -> str:
    """Normaliza rótulo/categoria para comparacao.

    Remove tudo que nao for letra/digito e passa para minusculo. Assim
    `HTTP401Unauthorized`, `HTTP 401 Unauthorized` e `http_401_unauthorized`
    casam com o mesmo conceito — o TTL mistura CamelCase e snake_case.
    """
    return re.sub(r"[^a-z0-9]", "", category.lower())


def _load_graph(ttl_path: str | None) -> Graph:
    path = ttl_path or settings.ontology_ttl_path
    graph = Graph()
    graph.parse(path, format="turtle")
    return graph


def _category_index(graph: Graph) -> dict[str, URIRef]:
    index: dict[str, URIRef] = {}
    for subject, _, label in graph.triples((None, SKOS.prefLabel, None)):
        if isinstance(label, str):
            # `triples()` tipa o sujeito como `Node`; o TTL usa URIRef.
            index[_normalize(str(label))] = URIRef(str(subject))
    return index


def _concept_iri(graph: Graph, category: str) -> URIRef | None:
    return _category_index(graph).get(_normalize(category))


def get_upper_categories_from_category(category: str, ttl_path: str | None = None) -> list[str]:
    """Infer upper categories from TTL SKOS hierarchy.

    Args:
        category: error category (e.g., "http_timeout", "HTTP401Unauthorized")
        ttl_path: path to error_codes.ttl (default: app/ontology/error_codes.ttl)

    Returns:
        List of upper category IRIs (e.g., ["http://example.org/iic/error_codes#NetworkError"])

    Example:
        >>> get_upper_categories_from_category("http_timeout")
        ["http://example.org/iic/error_codes#NetworkError"]
    """
    graph = _load_graph(ttl_path)
    concept = _concept_iri(graph, category)
    if concept is None:
        return []

    # Consulta via skos:broader*; `initBindings` evita montar `<uri>` na mao.
    query = """
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
    SELECT ?upper WHERE {
      ?concept skos:broader* ?upper .
      FILTER(?upper != ?concept)
    }
    """

    results = cast(Any, graph.query(query, initBindings={"concept": concept}))
    return [str(row["upper"]) for row in results]


def get_next_steps_from_category(category: str, ttl_path: str | None = None) -> list[str]:
    """Extract recommended next steps from TTL.

    O TTL atual guarda os passos em `skos:note "next_steps: ...; ..."`;
    `ex:next_steps` continua suportado para TTLs futuros/alternativos.

    Args:
        category: error category (e.g., "oauth_token_expired")
        ttl_path: path to error_codes.ttl

    Returns:
        List of next steps (e.g., ["Refresh token", "Reauth"])

    Example:
        >>> get_next_steps_from_category("oauth_token_expired")
        ["Renovar token OAuth2 via grant flow", ...]
    """
    graph = _load_graph(ttl_path)
    concept = _concept_iri(graph, category)
    if concept is None:
        return []

    # Forma canonica (TTLs novos): ex:next_steps.
    query = """
    PREFIX ex: <http://example.org/iic/error_codes#>
    SELECT ?step WHERE {
      ?concept ex:next_steps ?step .
    }
    """
    steps_query = cast(Any, graph.query(query, initBindings={"concept": concept}))
    steps = [str(row["step"]) for row in steps_query]
    if steps:
        return steps

    # Forma usada no error_codes.ttl atual: skos:note "next_steps: a; b; c".
    query = """
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
    SELECT ?note WHERE {
      ?concept skos:note ?note .
    }
    """
    notes_query = cast(Any, graph.query(query, initBindings={"concept": concept}))
    for row in notes_query:
        note = str(row["note"])
        if not note.lower().startswith(_NEXT_STEPS_PREFIX):
            continue
        body = note.split(":", 1)[1]
        steps.extend(step.strip() for step in body.split(";") if step.strip())
    return steps
