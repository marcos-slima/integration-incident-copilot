"""Script de avaliacao do RAG pipeline.

Metricas calculadas:
- Hit@1: documento correto e o top-1 resultado
- Hit@3: documento correto esta entre os top-3 resultados
- MRR: Mean Reciprocal Rank (posicao media do documento correto)
- Precision@3: dos 3 resultados, quantos sao relevantes
- Out-of-scope recall: casos sem documento esperado retornam confianca < 0.5

Uso:
    LD_LIBRARY_PATH=/usr/local/sap/nwrfcsdk/lib uv run python scripts/eval_rag.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.retriever import retrieve


def evaluate(dataset_path: str = "data/eval/rag_eval_dataset.json", top_k: int = 3) -> dict:
    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))

    in_scope = [d for d in dataset if d["difficulty"] != "out_of_scope"]
    out_of_scope = [d for d in dataset if d["difficulty"] == "out_of_scope"]

    hit1 = hit3 = mrr_total = 0
    precision_total = 0
    results = []

    print(f"\nAvaliando {len(in_scope)} casos in-scope + {len(out_of_scope)} out-of-scope\n")
    print(f"{'Query':<55} {'Dif':<8} {'Hit@1':<6} {'Hit@3':<6} {'MRR':<6} {'Top fonte'}")
    print("-" * 110)

    for case in in_scope:
        query = case["query"]
        expected = set(case["expected_sources"])
        difficulty = case["difficulty"]

        hits = retrieve(query, target="incidents", top_k=top_k)
        sources = [h["source"] for h in hits]

        # Hit@1
        h1 = int(bool(sources) and sources[0] in expected)
        hit1 += h1

        # Hit@3
        h3 = int(any(s in expected for s in sources))
        hit3 += h3

        # MRR
        rr = 0.0
        for i, s in enumerate(sources):
            if s in expected:
                rr = 1.0 / (i + 1)
                break
        mrr_total += rr

        # Precision@3
        relevant = sum(1 for s in sources if s in expected)
        p3 = relevant / top_k
        precision_total += p3

        top_source = sources[0] if sources else "nenhum"
        status = "✅" if h1 else ("⚠️" if h3 else "❌")

        print(
            f"{query[:54]:<55} {difficulty:<8} {status + '  ':<6} {'✅' if h3 else '❌' + '  ':<6} {rr:.2f}   {top_source[:40]}"
        )
        results.append(
            {**case, "hit1": h1, "hit3": h3, "mrr": rr, "precision3": p3, "retrieved": sources}
        )

    n = len(in_scope)
    print("\n" + "=" * 110)
    print(f"Hit@1:       {hit1}/{n} = {hit1 / n:.1%}")
    print(f"Hit@3:       {hit3}/{n} = {hit3 / n:.1%}")
    print(f"MRR:         {mrr_total / n:.3f}")
    print(f"Precision@3: {precision_total / n:.3f}")

    # Out-of-scope: verifica se score e baixo
    print(f"\nOut-of-scope ({len(out_of_scope)} casos):")
    oos_ok = 0
    for case in out_of_scope:
        hits = retrieve(case["query"], target="incidents", top_k=1)
        top_score = hits[0]["score"] if hits else 0.0
        ok = top_score < 0.7
        oos_ok += int(ok)
        status = "✅" if ok else "❌"
        print(f"  {status} score={top_score:.3f} query='{case['query'][:50]}'")

    print(f"\nOut-of-scope corretamente rejeitados: {oos_ok}/{len(out_of_scope)}")

    return {
        "hit_at_1": hit1 / n,
        "hit_at_3": hit3 / n,
        "mrr": mrr_total / n,
        "precision_at_3": precision_total / n,
        "oos_rejection_rate": oos_ok / len(out_of_scope) if out_of_scope else 1.0,
        "n_cases": n,
    }


if __name__ == "__main__":
    metrics = evaluate()
    print(f"\nMetricas finais: {json.dumps(metrics, indent=2)}")
