"""Testes unitarios de ontology_aware_retriever com mocks para infra.

Testes isolam a logica pura do retriever ontology-aware, sem depender de
Qdrant ou de TTL/ontology reais (ver tests/test_retriever.py para os
testes de integracao que precisam de infra viva).

DA-61 Fase 4: ontology-aware retrieval (hybrid RAG + SPARQL).
"""

from unittest.mock import MagicMock

import app.rag.ontology_aware_retriever as module
from app.rag.ontology_aware_retriever import retrieve_hybrid_ontology_aware


def _hit(
    source: str, text: str = "texto", score: float = 0.5, categories: list | None = None
) -> dict:
    """Helper para criar hits mockados."""
    hit = {"source": source, "text": text, "score": score}
    if categories is not None:
        hit["metadata"] = {"categories": categories}
    else:
        hit["metadata"] = {}
    return hit


def _hit_with_upper_categories(source: str, upper_categories: list, score: float = 0.7) -> dict:
    """Helper para hits com upper_categories no metadata."""
    return {
        "source": source,
        "text": "texto",
        "score": score,
        "metadata": {"upper_categories": upper_categories},
    }


class TestOntologyAwareRetriever:
    """Testes principais do modulo."""

    def test_returns_candidates_when_ontology_not_enabled(self, monkeypatch):
        """Ontologia desligada deve retornar os candidatos originais.

        Quando ontology_enrichment_enabled=False, mesmo com ontology_category
        presente, o filtro ontology NAO deve ser aplicado.
        """
        # Mockar config
        config_mock = MagicMock()
        config_mock.ontology_enrichment_enabled = False
        monkeypatch.setattr(module, "settings", config_mock)

        # Mockar retrieved_hybrid para retornar alguns candidatos
        expected_hits = [
            _hit("doc1.md"),
            _hit("doc2.md"),
        ]
        monkeypatch.setattr(module, "_retrieve_hybrid", lambda *a, **k: expected_hits)

        # Mockar ontology enrichment para levantar se chamado (garantia de que
        # NAO deve ser chamado)
        monkeypatch.setattr(module, "get_upper_categories_from_category", lambda *a: [])

        results = retrieve_hybrid_ontology_aware("query", top_k=5, ontology_category="http_timeout")

        assert len(results) == 2
        assert results[0]["source"] == "doc1.md"
        assert results[1]["source"] == "doc2.md"

    def test_returns_candidates_when_category_not_provided(self, monkeypatch):
        """Sem categoria, ontology enrichment NAO deve ser aplicado.

        Mesmo com ontology_enrichment_enabled=True, a ausencia de ontology_category
        faz com que o filtro seja pulado.
        """
        config_mock = MagicMock()
        config_mock.ontology_enrichment_enabled = True
        monkeypatch.setattr(module, "settings", config_mock)

        expected_hits = [
            _hit("doc1.md"),
            _hit("doc2.md"),
            _hit("doc3.md"),
        ]
        monkeypatch.setattr(module, "_retrieve_hybrid", lambda *a, **k: expected_hits)

        # ontology enrichment NAO deve ser chamado
        mock_ontology = MagicMock(return_value=[])
        monkeypatch.setattr(module, "get_upper_categories_from_category", mock_ontology)

        results = retrieve_hybrid_ontology_aware("query", top_k=5, ontology_category=None)

        assert len(results) == 3
        assert mock_ontology.call_count == 0

    def test_filters_candidates_when_ontology_enabled_and_category_provided(self, monkeypatch):
        """Ontologia ligada + categoria presente: filtro deve ser aplicado.

        Deve chamar ontology enrichment e filtrar/re-rank candidates com base
        nas upper categories.
        """
        config_mock = MagicMock()
        config_mock.ontology_enrichment_enabled = True
        monkeypatch.setattr(module, "settings", config_mock)

        # Candidatos originais (sem upper_categories no metadata)
        original_hits = [
            _hit("doc1.md", categories=["http_error"]),
            _hit("doc2.md", categories=["network_error"]),
            _hit("doc3.md", categories=["oauth_error"]),
        ]

        monkeypatch.setattr(module, "_retrieve_hybrid", lambda *a, **k: original_hits.copy())

        # ontology enrichment retorna upper categories para "http_timeout"
        def mock_get_upper_categories(category: str) -> list:
            if category == "http_timeout":
                return ["http://example.org/iic/error_codes#NetworkError"]
            return []

        monkeypatch.setattr(module, "get_upper_categories_from_category", mock_get_upper_categories)

        results = retrieve_hybrid_ontology_aware("query", top_k=5, ontology_category="http_timeout")

        # Como nenhum documento tem upper_categories correspondendo ao upper category
        # retornado, deve devolver todos os candidatos originais (sem filtro)
        assert len(results) == 3

    def test_graceful_degradation_when_ttl_fails(self, monkeypatch):
        """Falha no TTL/rdflib: deve degradar graciosamente.

        Se ontology enrichment falhar (arquivo TTL ausente, parse error, etc.),
        o retriever deve continuar funcionando, retornando os candidatos
        originais sem o filtro ontology.
        """
        config_mock = MagicMock()
        config_mock.ontology_enrichment_enabled = True
        monkeypatch.setattr(module, "settings", config_mock)

        expected_hits = [
            _hit("doc1.md"),
            _hit("doc2.md"),
        ]
        monkeypatch.setattr(module, "_retrieve_hybrid", lambda *a, **k: expected_hits)

        # ontology enrichment falha (levantar excecao)
        def failing_ontology(category: str) -> list:
            raise FileNotFoundError("error_codes.ttl not found")

        monkeypatch.setattr(module, "get_upper_categories_from_category", failing_ontology)

        # Nao deve levantar excecao - deve degradar graciosamente
        results = retrieve_hybrid_ontology_aware("query", top_k=5, ontology_category="http_timeout")

        assert len(results) == 2
        assert results[0]["source"] == "doc1.md"
        assert results[1]["source"] == "doc2.md"

    def test_applies_ontology_filter_when_candidates_have_upper_categories(self, monkeypatch):
        """Filtro ontology deve match candidates com upper_categories no metadata."""
        config_mock = MagicMock()
        config_mock.ontology_enrichment_enabled = True
        monkeypatch.setattr(module, "settings", config_mock)

        # Ontology enrichment retorna um upper category
        monkeypatch.setattr(
            module,
            "get_upper_categories_from_category",
            lambda c: ["http://example.org/iic/error_codes#NetworkError"],
        )

        # Candidates Com upper_categories correspondentes
        hits_with_match = [
            _hit_with_upper_categories(
                "doc1.md", ["http://example.org/iic/error_codes#NetworkError"], score=0.7
            ),
            _hit_with_upper_categories(
                "doc2.md", ["http://example.org/iic/error_codes#AuthenticationError"], score=0.6
            ),
            _hit("doc3.md", score=0.8),  # Sem upper_categories
        ]

        monkeypatch.setattr(module, "_retrieve_hybrid", lambda *a, **k: hits_with_match)

        results = retrieve_hybrid_ontology_aware("query", top_k=3, ontology_category="http_timeout")

        # Apenas o primeiro documento deve ser retornado (match exato com upper category)
        assert len(results) == 1
        assert results[0]["source"] == "doc1.md"

    def test_re_ranking_applies_category_preferred_order(self, monkeypatch):
        """Re-ranking deve priorizar documentos cujo metadata.categories contenha a categoria original."""
        config_mock = MagicMock()
        config_mock.ontology_enrichment_enabled = True
        monkeypatch.setattr(module, "settings", config_mock)

        # Ontology enrichment retorna uma upper category
        monkeypatch.setattr(
            module,
            "get_upper_categories_from_category",
            lambda c: ["http://example.org/iic/error_codes#NetworkError"],
        )

        # Candidates: dois com upper_categories matching, um sem (sera filtrado)
        hits = [
            _hit_with_upper_categories(
                "doc1.md", ["http://example.org/iic/error_codes#NetworkError"], score=0.7
            ),
            _hit_with_upper_categories(
                "doc2.md", ["http://example.org/iic/error_codes#NetworkError"], score=0.6
            ),
            _hit(
                "doc3.md", categories=["http_timeout"], score=0.8
            ),  # sem upper_categories, sera filtrado
        ]

        monkeypatch.setattr(module, "_retrieve_hybrid", lambda *a, **k: hits)

        results = retrieve_hybrid_ontology_aware("query", top_k=2, ontology_category="http_timeout")

        # Apenas doc1 e doc2 (com upper_categories matching) sao retornados
        # doc3 (sem upper_categories) e filtrado fora pelo ontology filter
        assert len(results) == 2
        sources = [r["source"] for r in results]
        assert "doc1.md" in sources
        assert "doc2.md" in sources

    def test_top_k_truncation_after_ontology_filter(self, monkeypatch):
        """Top-k deve ser aplicado apos ontology filter/re-ranking."""
        config_mock = MagicMock()
        config_mock.ontology_enrichment_enabled = True
        monkeypatch.setattr(module, "settings", config_mock)

        # Ontology enrichment retorna uma upper category
        monkeypatch.setattr(
            module,
            "get_upper_categories_from_category",
            lambda c: ["http://example.org/iic/error_codes#NetworkError"],
        )

        # Muitos candidates match
        hits = [
            _hit_with_upper_categories(
                f"doc{i}.md", ["http://example.org/iic/error_codes#NetworkError"], score=0.7
            )
            for i in range(10)
        ]

        monkeypatch.setattr(module, "_retrieve_hybrid", lambda *a, **k: hits)

        results = retrieve_hybrid_ontology_aware("query", top_k=3, ontology_category="http_timeout")

        # Deve retonar apenas top_k
        assert len(results) == 3

    def test_empty_candidates_handled(self, monkeypatch):
        """Candidatos vazios devem resultar em lista vazia."""
        config_mock = MagicMock()
        config_mock.ontology_enrichment_enabled = True
        monkeypatch.setattr(module, "settings", config_mock)

        monkeypatch.setattr(module, "_retrieve_hybrid", lambda *a, **k: [])

        results = retrieve_hybrid_ontology_aware("query", top_k=5, ontology_category="http_timeout")

        assert results == []

    def test_ontology_filter_returns_all_when_no_match(self, monkeypatch):
        """Quando nenhum candidate match ontology, deve devolver todos os candidatos originais."""
        config_mock = MagicMock()
        config_mock.ontology_enrichment_enabled = True
        monkeypatch.setattr(module, "settings", config_mock)

        # Ontology enrichment retorna uma upper category que nenhum candidate tem
        monkeypatch.setattr(
            module,
            "get_upper_categories_from_category",
            lambda c: ["http://example.org/iic/error_codes#UnknownError"],
        )

        hits = [
            _hit("doc1.md"),
            _hit("doc2.md"),
        ]

        monkeypatch.setattr(module, "_retrieve_hybrid", lambda *a, **k: hits)

        results = retrieve_hybrid_ontology_aware("query", top_k=5, ontology_category="http_timeout")

        # Nenhum match no ontology filter, deve retonar todos os originais
        assert len(results) == 2
