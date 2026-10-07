"""RAG-01 (validacao 2026-10-07): admissao pelo reranker e identidade do embedding."""

from __future__ import annotations

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from app.rag import embedding_guard as g
from app.rag.retriever import _evidence_admission_score


def test_r27_reranker_veta_cosseno_alto() -> None:
    """Antes: max(0.753, 0.0067) = 0.753 >= 0.5 -> admitido."""
    assert _evidence_admission_score({"score": 0.753, "rerank_score_calibrated": 0.0067}) < 0.5


def test_reranker_admite_cosseno_baixo_relevante() -> None:
    """O caso que motivou o max(): BM25 forte, cosseno baixo, reranker confiante."""
    assert _evidence_admission_score({"score": 0.47, "rerank_score_calibrated": 0.98}) >= 0.5


def test_sem_reranker_vale_o_cosseno() -> None:
    assert _evidence_admission_score({"score": 0.62}) == pytest.approx(0.62)


def test_r15_verify_once_nao_silencia_depois_do_erro() -> None:
    c = QdrantClient(":memory:")
    c.create_collection("col", vectors_config=VectorParams(size=4, distance=Distance.COSINE))
    g.stamp_collection(c, "col", "BAAI/bge-small-en-v1.5", provider="fastembed")
    g.reset_verification_cache()
    for _ in range(2):
        with pytest.raises(g.EmbeddingMismatchError):
            g.verify_once(c, "col", "nomic-embed-text", provider="ollama")
    g.verify_once(c, "col", "BAAI/bge-small-en-v1.5", provider="fastembed")


def test_identidade_reflete_o_backend(monkeypatch) -> None:
    import app.rag.retriever as r

    monkeypatch.setattr(r, "_EMBEDDING_BACKEND", "fastembed")
    assert r.embedding_identity() == ("fastembed", r.FASTEMBED_MODEL)
    monkeypatch.setattr(r, "_EMBEDDING_BACKEND", "ollama")
    assert r.embedding_identity() == ("ollama", r.EMBEDDING_MODEL)


# ---------------------------------------------------------------------------
# DEP-01: BM25 offline via FASTEMBED_BM25_PATH
# ---------------------------------------------------------------------------


def test_bm25_usa_caminho_local_quando_preenchido(tmp_path, monkeypatch):
    from app.rag import retriever

    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    chamadas = []
    monkeypatch.setattr(
        retriever, "SparseTextEmbedding", lambda **kw: chamadas.append(kw) or object()
    )
    monkeypatch.setenv("FASTEMBED_BM25_PATH", str(tmp_path))
    retriever.new_sparse_model()
    assert chamadas[-1]["specific_model_path"] == str(tmp_path)


def test_bm25_ignora_caminho_vazio(tmp_path, monkeypatch):
    """Imagem com PRELOAD_MODELS=0: o diretorio existe mas esta vazio."""
    from app.rag import retriever

    chamadas = []
    monkeypatch.setattr(
        retriever, "SparseTextEmbedding", lambda **kw: chamadas.append(kw) or object()
    )
    monkeypatch.setenv("FASTEMBED_BM25_PATH", str(tmp_path))
    retriever.new_sparse_model()
    assert "specific_model_path" not in chamadas[-1]
