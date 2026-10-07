"""Quality gate do RAG pipeline.

Este teste roda o dataset de avaliacao completo e falha se as metricas
caírem abaixo dos thresholds definidos. Requer Qdrant com a base de
avaliacao ingerida (job `rag-quality` do CI, EMBEDDING_BACKEND=fastembed).

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


def _qdrant_up() -> bool:
    import urllib.request

    from app.config import settings

    try:
        urllib.request.urlopen(settings.qdrant_url.rstrip("/") + "/healthz", timeout=2)
        return True
    except Exception:  # noqa: BLE001
        return False


# Validacao 2026-10-07 (CI-01): o marker `integration` fazia o conftest
# pular este teste sempre que nao havia Ollama na 11434 - inclusive no job
# `rag-quality` do CI, que roda com EMBEDDING_BACKEND=fastembed e NAO tem
# Ollama. O job ficava verde sem medir nada. Agora so depende do Qdrant.
@pytest.mark.rag_quality
def test_rag_quality_gate():
    """Falha se metricas RAG caírem abaixo dos thresholds."""
    import os

    # Thresholds medidos com fastembed (bge-small) sobre data/sample_docs - o
    # mesmo setup do job do CI. Fora dele (ex.: Qdrant local com o acervo e o
    # nomic do desenvolvedor) o numero nao e comparavel: opt-in explicito.
    if os.environ.get("EMBEDDING_BACKEND", "").lower() != "fastembed" and not os.environ.get(
        "RAG_QUALITY_GATE"
    ):
        pytest.skip("rode com EMBEDDING_BACKEND=fastembed (setup do job rag-quality)")
    if not _qdrant_up():
        pytest.skip("Qdrant indisponivel (QDRANT_URL)")
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
