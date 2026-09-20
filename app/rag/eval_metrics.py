"""Metricas de ranking usadas no benchmark de rerankers (scripts/benchmark_rerankers.py)
e reutilizaveis por scripts/eval_rag.py.

Isoladas num modulo proprio, SEM nenhuma dependencia de infraestrutura
(Qdrant/Ollama/HTTP) - funcoes puras sobre listas de ids de documento,
testaveis sem rede nem modelo carregado (ver tests/test_eval_metrics.py).
Mesmo principio de separar logica pura de infraestrutura usado em
app.rag.retriever._evidence_admission_score.

Relevancia binaria (um documento ou e relevante pra uma query, ou nao -
nao ha graus de relevancia no dataset de avaliacao deste projeto,
`data/eval/rag_eval_dataset.json`, que usa `expected_sources: list[str]`).
"""

from __future__ import annotations

import math


def hit_at_1(ranked_sources: list[str], expected_sources: set[str]) -> int:
    """1 se o primeiro resultado da lista ranqueada e relevante, senao 0."""
    if not ranked_sources:
        return 0
    return int(ranked_sources[0] in expected_sources)


def recall_at_k(ranked_sources: list[str], expected_sources: set[str], k: int) -> float:
    """Fracao dos documentos relevantes que aparecem nos top-k resultados.

    0.0 se `expected_sources` estiver vazio (nao ha o que recuperar -
    evita ZeroDivisionError; o caller decide se um caso assim entra na
    media geral)."""
    if not expected_sources:
        return 0.0
    top_k = set(ranked_sources[:k])
    return len(top_k & expected_sources) / len(expected_sources)


def mrr_at_k(ranked_sources: list[str], expected_sources: set[str], k: int) -> float:
    """Reciprocal rank (1/posicao) do primeiro documento relevante dentro
    dos top-k, ou 0.0 se nenhum relevante aparecer nos top-k."""
    for i, source in enumerate(ranked_sources[:k], start=1):
        if source in expected_sources:
            return 1.0 / i
    return 0.0


def _dcg(relevances: list[int]) -> float:
    """DCG classico com relevancia binaria: soma de rel_i / log2(i+1),
    i comecando em 1 (posicao 1 = i=1 -> log2(2) = 1, sem singularidade)."""
    return sum(rel / math.log2(i + 1) for i, rel in enumerate(relevances, start=1))


def ndcg_at_k(ranked_sources: list[str], expected_sources: set[str], k: int) -> float:
    """nDCG@k com relevancia binaria. IDCG = DCG da ordenacao ideal
    (todos os relevantes primeiro, ate min(k, len(expected_sources))) -
    normaliza para 0.0-1.0 independente de quantos documentos relevantes
    existem. 0.0 se `expected_sources` estiver vazio."""
    if not expected_sources:
        return 0.0
    relevances = [1 if s in expected_sources else 0 for s in ranked_sources[:k]]
    dcg = _dcg(relevances)
    ideal_relevances = [1] * min(k, len(expected_sources))
    idcg = _dcg(ideal_relevances)
    return dcg / idcg if idcg > 0 else 0.0
