"""Testes das metricas de ranking (app/rag/eval_metrics.py) - logica
pura, sem modelo carregado nem infraestrutura, usada pelo benchmark de
rerankers (scripts/benchmark_rerankers.py)."""

from app.rag.eval_metrics import hit_at_1, mrr_at_k, ndcg_at_k, recall_at_k


def test_hit_at_1_true_when_top_result_is_relevant():
    assert hit_at_1(["a.md", "b.md"], {"a.md"}) == 1


def test_hit_at_1_false_when_top_result_is_not_relevant():
    assert hit_at_1(["b.md", "a.md"], {"a.md"}) == 0


def test_hit_at_1_false_when_ranked_list_is_empty():
    assert hit_at_1([], {"a.md"}) == 0


def test_recall_at_k_full_recall_when_all_relevant_are_in_top_k():
    assert recall_at_k(["a.md", "b.md", "c.md"], {"a.md", "b.md"}, k=3) == 1.0


def test_recall_at_k_partial_recall_when_only_some_relevant_are_in_top_k():
    # so "a.md" (1 dos 2 relevantes) esta nos top-2
    assert recall_at_k(["a.md", "x.md", "b.md"], {"a.md", "b.md"}, k=2) == 0.5


def test_recall_at_k_zero_when_no_relevant_in_top_k():
    assert recall_at_k(["x.md", "y.md"], {"a.md"}, k=2) == 0.0


def test_recall_at_k_zero_when_expected_sources_empty():
    assert recall_at_k(["a.md"], set(), k=5) == 0.0


def test_mrr_at_k_reciprocal_of_first_relevant_position():
    assert mrr_at_k(["x.md", "a.md", "y.md"], {"a.md"}, k=5) == 0.5  # posicao 2


def test_mrr_at_k_one_when_top_result_is_relevant():
    assert mrr_at_k(["a.md", "b.md"], {"a.md"}, k=5) == 1.0


def test_mrr_at_k_zero_when_no_relevant_within_k():
    assert mrr_at_k(["x.md", "y.md", "a.md"], {"a.md"}, k=2) == 0.0  # relevante so na posicao 3


def test_ndcg_at_k_one_when_ranking_is_ideal():
    # os 2 relevantes ja vem primeiro - DCG == IDCG
    assert ndcg_at_k(["a.md", "b.md", "x.md"], {"a.md", "b.md"}, k=3) == 1.0


def test_ndcg_at_k_less_than_one_when_relevant_is_not_first():
    score = ndcg_at_k(["x.md", "a.md"], {"a.md"}, k=2)
    assert 0.0 < score < 1.0


def test_ndcg_at_k_zero_when_no_relevant_in_top_k():
    assert ndcg_at_k(["x.md", "y.md"], {"a.md"}, k=2) == 0.0


def test_ndcg_at_k_zero_when_expected_sources_empty():
    assert ndcg_at_k(["a.md"], set(), k=5) == 0.0


def test_ndcg_at_k_caps_ideal_at_min_k_and_relevant_count():
    # 3 documentos relevantes existem, mas k=2 - IDCG so considera os 2
    # primeiros (nao da pra encaixar os 3 nos top-2) - com os 2
    # primeiros da lista ja sendo relevantes, o ranking e ideal (1.0)
    # mesmo sem o terceiro relevante aparecer.
    assert ndcg_at_k(["a.md", "b.md", "x.md"], {"a.md", "b.md", "c.md"}, k=2) == 1.0
