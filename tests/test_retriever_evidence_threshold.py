"""DA-25: o threshold de confianca (score_threshold) nao pode mais ser
aplicado ANTES do reranker (cross-encoder) - um documento com BM25/RRF
forte mas cosseno denso moderado precisa ter a chance de ser avaliado
pelo reranker antes de ser descartado. Estes testes isolam a logica
pura (_evidence_admission_score) e o fluxo de _retrieve_unified via
monkeypatch dos limites de infraestrutura (_retrieve_hybrid,
_retrieve_dense_only, rerank, _get_qdrant_client) - sem depender de
Qdrant/Ollama reais (ver tests/test_retriever.py para os testes de
integracao que precisam de infra viva).
"""

import pytest

import app.rag.retriever as retriever_module
from app.rag.retriever import _evidence_admission_score, _retrieve_unified


def _hit(source: str, text: str = "texto", score: float = 0.5, rerank_score=None) -> dict:
    hit = {"source": source, "text": text, "score": score, "cosine_score": score}
    if rerank_score is not None:
        hit["rerank_score"] = rerank_score
    return hit


# ---------------------------------------------------------------------
# _evidence_admission_score - logica pura
# ---------------------------------------------------------------------


def test_admission_score_uses_cosine_when_no_rerank_score():
    hit = _hit("doc.md", score=0.62)
    assert _evidence_admission_score(hit) == 0.62


def test_admission_score_uses_rerank_when_higher_than_cosine():
    # Caso relatado na revisao externa: BM25/rerank forte, cosseno
    # moderado (0.47) - o documento deve ser admitido pelo rerank_score
    # clampado, nao pelo cosseno fraco.
    hit = _hit("doc.md", score=0.47, rerank_score=8.5)
    # RAG-01 (2026-10-07): o logit passa pela sigmoid de rerank(), nao por clamp
    assert _evidence_admission_score(hit) > 0.99


def test_admission_score_reranker_negativo_veta_mesmo_com_cosseno():
    # RAG-01 (2026-10-07): com reranker, a decisao e dele. Antes o cosseno
    # "vencia" e admitia o que o cross-encoder julgou irrelevante.
    hit = _hit("doc.md", score=0.3, rerank_score=-5.0)
    assert _evidence_admission_score(hit) < 0.01


def test_admission_score_usa_o_reranker_e_nao_o_maximo():
    import math

    hit = _hit("doc.md", score=0.2, rerank_score=0.6)
    assert _evidence_admission_score(hit) == pytest.approx(1 / (1 + math.exp(-0.6)))


# ---------------------------------------------------------------------
# _retrieve_unified - fluxo completo com infra mockada
# ---------------------------------------------------------------------


def test_weak_cosine_hit_admitted_when_reranker_scores_it_highly(monkeypatch):
    """O caso central da correcao DA-25: um hit com cosseno abaixo do
    threshold (0.47 < 0.5) so sobrevive porque o reranker (mockado)
    avalia o par como fortemente relevante."""
    weak_hit = _hit("idoc_status_51.md", text="IDoc parado status 51", score=0.47)

    monkeypatch.setattr(retriever_module, "_retrieve_hybrid", lambda *a, **k: [weak_hit])

    def fake_rerank(query, hits, top_k):
        for h in hits:
            h["rerank_score"] = 9.0  # cross-encoder: fortemente relevante
        return sorted(hits, key=lambda h: h["rerank_score"], reverse=True)[:top_k]

    monkeypatch.setattr(retriever_module, "rerank", fake_rerank)

    # cosseno (0.47) fica abaixo do score_threshold, entao o fluxo
    # tentaria consultar reference_library como fallback - mockamos o
    # client pra nao bater em Qdrant real neste teste unitario.
    class _NoReferenceClient:
        def get_collection(self, *_args, **_kwargs):
            raise ValueError("reference_library indisponivel neste teste")

    monkeypatch.setattr(retriever_module, "_get_qdrant_client", lambda: _NoReferenceClient())

    results = _retrieve_unified("IDoc parado status 51", top_k=3, score_threshold=0.5)

    assert len(results) == 1
    assert results[0]["source"] == "idoc_status_51.md"
    assert results[0]["rerank_score"] == 9.0


def test_weak_hit_rejected_when_reranker_also_scores_it_low(monkeypatch):
    """Sem o reranker "salvar" o documento, cosseno fraco continua
    sendo rejeitado - a correcao nao vira uma porta aberta para
    qualquer coisa entrar."""
    weak_hit = _hit("irrelevant.md", text="totalmente sem relacao", score=0.3)

    monkeypatch.setattr(retriever_module, "_retrieve_hybrid", lambda *a, **k: [weak_hit])

    def fake_rerank(query, hits, top_k):
        for h in hits:
            h["rerank_score"] = -6.0  # cross-encoder: irrelevante
        return sorted(hits, key=lambda h: h["rerank_score"], reverse=True)[:top_k]

    monkeypatch.setattr(retriever_module, "rerank", fake_rerank)

    class _NoReferenceClient:
        def get_collection(self, *_args, **_kwargs):
            raise ValueError("reference_library indisponivel neste teste")

    monkeypatch.setattr(retriever_module, "_get_qdrant_client", lambda: _NoReferenceClient())

    results = _retrieve_unified("query qualquer", top_k=3, score_threshold=0.5)

    assert results == []


def test_reference_library_fallback_skipped_when_incidents_has_strong_match(monkeypatch):
    strong_hit = _hit("cpi_http_401.md", score=0.9)
    monkeypatch.setattr(retriever_module, "_retrieve_hybrid", lambda *a, **k: [strong_hit])
    monkeypatch.setattr(retriever_module, "rerank", lambda query, hits, top_k: hits[:top_k])

    called = {"get_collection": False}

    class _FakeClient:
        def get_collection(self, *_args, **_kwargs):
            called["get_collection"] = True
            raise AssertionError("nao deveria consultar reference_library")

    monkeypatch.setattr(retriever_module, "_get_qdrant_client", lambda: _FakeClient())

    results = _retrieve_unified("erro 401 no iFlow", top_k=3, score_threshold=0.5)

    assert called["get_collection"] is False
    assert len(results) == 1
    assert results[0]["source"] == "cpi_http_401.md"


def test_reference_library_fallback_triggered_when_incidents_has_no_strong_match(monkeypatch):
    weak_incidents_hit = _hit("incidents_weak.md", score=0.2)
    strong_reference_hit = _hit("Manual_Basis_SAP.pdf", score=0.9)

    monkeypatch.setattr(retriever_module, "_retrieve_hybrid", lambda *a, **k: [weak_incidents_hit])
    monkeypatch.setattr(retriever_module, "rerank", lambda query, hits, top_k: hits[:top_k])
    monkeypatch.setattr(
        retriever_module, "_retrieve_dense_only", lambda *a, **k: [strong_reference_hit]
    )

    class _FakeCollectionInfo:
        points_count = 100

    class _FakeClient:
        def get_collection(self, *_args, **_kwargs):
            return _FakeCollectionInfo()

    monkeypatch.setattr(retriever_module, "_get_qdrant_client", lambda: _FakeClient())

    results = _retrieve_unified("query fora do escopo de incidents", top_k=3, score_threshold=0.5)

    sources = {r["source"] for r in results}
    assert "Manual_Basis_SAP.pdf" in sources


def test_reference_library_missing_collection_degrades_gracefully(monkeypatch):
    """Ambiente novo sem a biblioteca indexada: o Qdrant responde 404
    (UnexpectedResponse) ao consultar sap_reference_library. O fallback
    deve ser ignorado com log, sem derrubar o diagnostico com 500."""
    from qdrant_client.http.exceptions import UnexpectedResponse

    weak_hit = _hit("incidents_weak.md", score=0.2)
    monkeypatch.setattr(retriever_module, "_retrieve_hybrid", lambda *a, **k: [weak_hit])
    monkeypatch.setattr(retriever_module, "rerank", lambda query, hits, top_k: hits[:top_k])

    class _MissingCollectionClient:
        def get_collection(self, *_args, **_kwargs):
            raise UnexpectedResponse(
                status_code=404,
                reason_phrase="Not Found",
                content=b'{"status":{"error":"Not found: Collection `sap_reference_library` doesn\'t exist!"}}',
                headers={},
            )

    monkeypatch.setattr(retriever_module, "_get_qdrant_client", lambda: _MissingCollectionClient())

    # Nao deve levantar excecao; o hit fraco de incidents segue para o
    # filtro de admissao normal (e aqui e descartado por ser fraco).
    results = _retrieve_unified("query sem match forte", top_k=3, score_threshold=0.5)

    assert isinstance(results, list)
