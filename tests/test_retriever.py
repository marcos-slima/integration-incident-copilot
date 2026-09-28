"""Testes de integracao do retriever RAG - precisam de Qdrant vivo
com a collection sap_incident_docs ja indexada (ver README/manual).
"""

import pytest

from app.rag.retriever import retrieve


@pytest.mark.integration
@pytest.mark.parametrize(
    "query,expected_source,min_score",
    [
        ("erro 401 no iFlow", "cpi_http_401.md", 0.6),
        ("iFlow travando ao consumir OData sem retorno", "odata_timeout_cpi.md", 0.6),
        ("IDoc parado com status 51", "idoc_status_51.md", 0.6),
        ("SM59 dando erro de conexao recusada", "rfc_connection_refused.md", 0.6),
    ],
)
def test_retriever_finds_correct_document(query, expected_source, min_score):
    hits = retrieve(query, target="incidents", top_k=3)
    assert hits, f"Nenhum resultado para a query: {query}"

    top = hits[0]
    assert (
        top["source"] == expected_source
    ), f"Esperado '{expected_source}' como top-1 para '{query}', veio '{top['source']}'"
    assert top["score"] >= min_score
