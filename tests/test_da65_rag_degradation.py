"""Testes DA-65: degradação graciosa do RAG quando Qdrant cai.

A regra é simples: `retrieve_node` nunca propaga exceção de Qdrant —
sempre retorna lista vazia, permitindo que o rule engine e/ou LLM
continuem operando sem contexto RAG.
"""

from unittest.mock import MagicMock, patch

from qdrant_client.http.exceptions import (
    ResponseHandlingException,
    UnexpectedResponse,
)
from qdrant_client.http.models import SparseVector


def _make_unexpected_response():
    """Helper para criar UnexpectedResponse com a assinatura correta."""
    return UnexpectedResponse(
        status_code=503,
        reason_phrase="Service Unavailable",
        content=b"Qdrant unreachable",
        headers=MagicMock(),
    )


class TestQdrantFailureGracefulDegradation:
    """DA-65: quando Qdrant cai, o pipeline continua."""

    def test_retrieve_hybrid_returns_empty_on_qdrant_failure(self):
        """Perna hibrida: exceção de Qdrant → lista vazia (não propaga)."""
        from app.rag.retriever import _retrieve_hybrid

        with (
            patch("app.rag.retriever._get_qdrant_client") as mock_client,
            patch(
                "app.rag.retriever.embedding_identity",
                return_value=("fastembed", "BAAI/bge-small-en-v1.5"),
            ),
            patch("app.rag.retriever._get_embeddings") as mock_emb,
            # IIC-AUD-107: vetor esparso stubado - sem rede, o Qdrant/bm25 real
            # nao carrega e o teste falhava antes de chegar no Qdrant.
            patch(
                "app.rag.retriever._sparse_query_vector",
                return_value=SparseVector(indices=[1], values=[1.0]),
            ),
        ):
            mock_emb.return_value.embed_query.return_value = [0.1] * 384

            # Qdrant client lança exceção (simula indisponibilidade)
            mock_client.return_value.query_points.side_effect = _make_unexpected_response()

            result = _retrieve_hybrid("test query", "incidents", top_k=5)

        # A regra DA-65: lista vazia, não exceção propagada
        assert result == []

    def test_retrieve_dense_only_returns_empty_on_qdrant_failure(self):
        """Perna densa: exceção de Qdrant → lista vazia (não propaga)."""
        from app.rag.retriever import _retrieve_dense_only

        with (
            patch("app.rag.retriever._get_qdrant_client") as mock_client,
            patch(
                "app.rag.retriever.embedding_identity",
                return_value=("fastembed", "BAAI/bge-small-en-v1.5"),
            ),
            patch("app.rag.retriever._get_embeddings") as mock_emb,
        ):
            mock_emb.return_value.embed_query.return_value = [0.1] * 384

            # ResponseHandlingException recebe uma exceção como source
            inner_exc = Exception("connection refused")
            mock_client.return_value.query_points.side_effect = ResponseHandlingException(inner_exc)

            result = _retrieve_dense_only("test query", "incidents", top_k=5, score_threshold=0.0)

        assert result == []


class TestEmbedderFailureGracefulDegradation:
    """IIC-AUD-419: Ollama (embedder) fora do ar degrada como o Qdrant fora
    do ar - lista vazia, sem 500 - e erro de aplicacao continua propagando."""

    def _patches(self, emb_side_effect):
        return (
            patch("app.rag.retriever._get_qdrant_client"),
            patch(
                "app.rag.retriever.embedding_identity",
                return_value=("ollama", "nomic-embed-text"),
            ),
            patch("app.rag.retriever.verify_once"),
            patch(
                "app.rag.retriever._get_embeddings",
                return_value=MagicMock(embed_query=MagicMock(side_effect=emb_side_effect)),
            ),
        )

    def test_hybrid_sem_embedder_retorna_vazio(self):
        import httpx

        from app.rag.retriever import _retrieve_hybrid

        for erro in (
            ConnectionError("Failed to connect to Ollama"),
            httpx.ConnectError("refused"),
            httpx.ReadTimeout("timeout"),
        ):
            p1, p2, p3, p4 = self._patches(erro)
            with p1 as client, p2, p3, p4:
                assert _retrieve_hybrid("q", "incidents", top_k=3) == []
                client.return_value.query_points.assert_not_called()

    def test_dense_only_sem_embedder_retorna_vazio(self):
        from app.rag.retriever import _retrieve_dense_only

        p1, p2, p3, p4 = self._patches(ConnectionError("Failed to connect to Ollama"))
        with p1, p2, p3, p4:
            assert _retrieve_dense_only("q", "reference", top_k=3, score_threshold=0.5) == []

    def test_erro_de_aplicacao_do_embedder_propaga(self):
        import pytest

        from app.rag.retriever import _retrieve_hybrid

        p1, p2, p3, p4 = self._patches(ValueError("model 'x' not found"))
        with p1, p2, p3, p4, pytest.raises(ValueError):
            _retrieve_hybrid("q", "incidents", top_k=3)
