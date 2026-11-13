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


def paired_hit_comparison(
    candidate_hits: list[int], baseline_hits: list[int], *, rounds: int = 10_000, seed: int = 42
) -> dict[str, float | int]:
    """Validacao 2026-10-07 (M-20): diferenca de Hit@1 com incerteza.

    O reranker foi escolhido por uma margem de UMA consulta (12/13 vs 11/13)
    apresentada como "+7pp". Com n tao pequeno a diferenca precisa vir com:
      - os pares discordantes (b: so o candidato acerta; c: so o baseline);
      - p-valor exato de McNemar (binomial bicaudal sobre b+c);
      - IC 95% por bootstrap pareado (reamostra consultas, nao modelos).
    Puro (sem numpy) e deterministico (seed), para o JSON ser reproduzivel.
    """
    import random
    from math import comb

    if len(candidate_hits) != len(baseline_hits) or not candidate_hits:
        raise ValueError("listas de acertos pareadas e nao vazias")
    n = len(candidate_hits)
    b = sum(1 for x, y in zip(candidate_hits, baseline_hits, strict=True) if x and not y)
    c = sum(1 for x, y in zip(candidate_hits, baseline_hits, strict=True) if y and not x)
    discordant = b + c
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(comb(discordant, k) for k in range(min(b, c) + 1)) / 2**discordant
        p_value = min(1.0, 2 * tail)
    rng = random.Random(seed)
    diffs = []
    for _ in range(rounds):
        idx = [rng.randrange(n) for _ in range(n)]
        diffs.append(sum(candidate_hits[i] - baseline_hits[i] for i in idx) / n)
    diffs.sort()
    return {
        "n": n,
        "delta_hit_at_1": (sum(candidate_hits) - sum(baseline_hits)) / n,
        "only_candidate": b,
        "only_baseline": c,
        "mcnemar_exact_p": round(p_value, 4),
        "bootstrap_ci95_low": diffs[int(0.025 * rounds)],
        "bootstrap_ci95_high": diffs[int(0.975 * rounds) - 1],
    }
