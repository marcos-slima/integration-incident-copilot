"""IIC-AUD-302: testes contra o `error_codes.ttl` real (sem mock).

Os testes antigos de `ontology_aware_retriever` fazem monkeypatch das duas
funcoes, entao validavam o fluxo, nao a implementacao. Estes exercitam o TTL
versionado no repositorio.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.ontology.enrichment import (
    get_next_steps_from_category,
    get_upper_categories_from_category,
)

TTL = Path(__file__).resolve().parents[1] / "app/ontology/error_codes.ttl"


def _iri(name: str) -> str:
    return f"http://example.org/iic/error_codes#{name}"


def test_upper_categories_depend_on_input():
    """Duas categorias com pais diferentes devolvem listas diferentes."""
    network = get_upper_categories_from_category("http_timeout", str(TTL))
    auth = get_upper_categories_from_category("oauth_token_expired", str(TTL))

    assert network == [_iri("NetworkError")]
    assert auth == [_iri("AuthenticationError")]
    assert network != auth


def test_camel_case_and_label_normalization_match():
    """`HTTP401Unauthorized` casa com o label `HTTP 401 Unauthorized`."""
    assert get_upper_categories_from_category("HTTP401Unauthorized", str(TTL)) == [
        _iri("AuthenticationError")
    ]
    assert get_upper_categories_from_category("HTTP 401 Unauthorized", str(TTL)) == [
        _iri("AuthenticationError")
    ]


def test_unknown_category_returns_empty():
    assert get_upper_categories_from_category("XYZ_INEXISTENTE_123", str(TTL)) == []
    assert get_next_steps_from_category("XYZ_INEXISTENTE_123", str(TTL)) == []


def test_next_steps_read_from_skos_note():
    """O TTL guarda os passos em `skos:note "next_steps: ..."`."""
    steps = get_next_steps_from_category("http_timeout", str(TTL))

    assert steps
    assert "Aumentar timeout no client ou proxy" in steps
    assert all(not step.lower().startswith("next_steps:") for step in steps)


def test_queries_do_not_make_rdflib_serialize_invalid_uri(caplog):
    """`initBindings` nao pode produzir o aviso 'does not look like a valid URI'."""
    with caplog.at_level(logging.WARNING, logger="rdflib.term"):
        get_upper_categories_from_category("http_timeout", str(TTL))
        get_next_steps_from_category("http_timeout", str(TTL))

    assert not [r for r in caplog.records if "valid URI" in r.getMessage()]
