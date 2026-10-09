"""Testes unitarios de ontology_agent_node - DA-61 Fase 5.

Testes isolam a logica do node ontology_agent_node, sem depender de
ontology_ttl_path real ou de Qdrant (ver test_ontology_aware_retriever.py
para os testes de integracao do retriever).
"""

from unittest.mock import MagicMock

from app.agent.ontology_agent import ontology_agent_node


class TestOntologyAgentNode:
    """Testes principais do ontology_agent_node."""

    def test_returns_empty_candidates_when_ontology_disabled(self, monkeypatch):
        """Ontologia desligada deve retornar dicionario vazio."""
        config_mock = MagicMock()
        config_mock.ontology_enrichment_enabled = False
        monkeypatch.setattr("app.agent.ontology_agent.settings", config_mock)

        result = ontology_agent_node({"diagnosis": {"rule_engine_category": "HTTP401Unauthorized"}})

        assert result == {"ontology_candidates": {}}

    def test_returns_empty_candidates_when_no_category(self, monkeypatch):
        """Sem rule_engine_category deve retornar dicionario vazio."""
        config_mock = MagicMock()
        config_mock.ontology_enrichment_enabled = True
        monkeypatch.setattr("app.agent.ontology_agent.settings", config_mock)

        # ontology retrieval NAO deve ser chamado
        monkeypatch.setattr(
            "app.agent.ontology_agent.retrieve_hybrid_ontology_aware",
            lambda *a, **k: [],
        )

        result = ontology_agent_node({"diagnosis": {}})

        assert result == {"ontology_candidates": {}}

    def test_returns_candidates_when_ontology_enabled_and_category_present(self, monkeypatch):
        """Ontologia ligada + categoria presente: deve chamar retrieve_hybrid_ontology_aware."""
        config_mock = MagicMock()
        config_mock.ontology_enrichment_enabled = True
        monkeypatch.setattr("app.agent.ontology_agent.settings", config_mock)

        # Mockar retrieve_hybrid_ontology_aware para retornar candidatos
        expected_candidates = [
            {"source": "doc1.md", "text": "texto 1", "score": 0.8, "metadata": {}},
            {"source": "doc2.md", "text": "texto 2", "score": 0.7, "metadata": {}},
        ]

        mock_retrieve = MagicMock(return_value=expected_candidates)
        monkeypatch.setattr(
            "app.agent.ontology_agent.retrieve_hybrid_ontology_aware",
            mock_retrieve,
        )

        result = ontology_agent_node({"diagnosis": {"rule_engine_category": "HTTP401Unauthorized"}})

        # Verificar chamada ao retrieval
        mock_retrieve.assert_called_once()
        assert mock_retrieve.call_args[1]["ontology_category"] == "HTTP401Unauthorized"
        assert mock_retrieve.call_args[1]["top_k"] == 5

        # Verificar retorno formatado como dicionario
        assert "ontology_candidates" in result
        candidates = result["ontology_candidates"]
        assert "candidate_0" in candidates
        assert "candidate_1" in candidates
        assert candidates["candidate_0"]["source"] == "doc1.md"
        assert candidates["candidate_0"]["score"] == 0.8

    def test_graceful_degradation_on_ttl_error(self, monkeypatch):
        """Falha no TTL/rdflib deve degradar graciosamente."""
        config_mock = MagicMock()
        config_mock.ontology_enrichment_enabled = True
        monkeypatch.setattr("app.agent.ontology_agent.settings", config_mock)

        def failing_retrieve(*a, **k):
            raise FileNotFoundError("error_codes.ttl not found")

        monkeypatch.setattr(
            "app.agent.ontology_agent.retrieve_hybrid_ontology_aware",
            failing_retrieve,
        )

        # Nao deve levantar excecao
        result = ontology_agent_node({"diagnosis": {"rule_engine_category": "HTTP401Unauthorized"}})

        assert result == {"ontology_candidates": {}}
