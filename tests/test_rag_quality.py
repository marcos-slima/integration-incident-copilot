"""Quality gate do RAG pipeline.

Este teste roda o dataset de avaliacao completo e falha se as metricas
caírem abaixo dos thresholds definidos. Marcado como 'integration' porque
requer Qdrant e Ollama rodando localmente.

Thresholds baseados no baseline medido em 2026-09-15:
  Hit@1:  92.3% -> threshold conservador: 85%
  Hit@3: 100.0% -> threshold conservador: 92%
  MRR:    0.949 -> threshold conservador: 0.85
"""

import pytest

THRESHOLDS = {
    "hit_at_1": 0.85,
    "hit_at_3": 0.92,
    "mrr": 0.85,
    "oos_rejection_rate": 0.40,
}


@pytest.mark.integration
def test_rag_quality_gate():
    """Falha se metricas RAG caírem abaixo dos thresholds."""
    from scripts.eval_rag import evaluate

    metrics = evaluate()

    failures = []
    for metric, threshold in THRESHOLDS.items():
        value = metrics.get(metric, 0.0)
        if value < threshold:
            failures.append(f"{metric}: {value:.3f} < threshold {threshold:.3f}")

    if failures:
        pytest.fail(
            "RAG quality gate falhou — metricas abaixo do threshold:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )

    print("\nRAG quality gate passou:")
    for metric, threshold in THRESHOLDS.items():
        value = metrics.get(metric, 0.0)
        print(f"  {metric}: {value:.3f} >= {threshold:.3f} ✅")
