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
