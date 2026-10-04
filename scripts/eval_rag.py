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

    # Out-of-scope: verifica se o pipeline NAO admite o caso.
    #
    # DA-42: medir isto no `score` (cosseno denso) estava errado por dois
    # motivos, e o primeiro nao e' opiniao. Medido no corpus de avaliacao
    # (fastembed, Qdrant limpo): o cosseno dos in-scope vai de 0.719 a 0.871 e
    # o dos out-of-scope e' 0.753 e 0.779 — os grupos se SOBREPOEM. Nenhum
    # limiar de cosseno separa os dois; o unico que faria (0.780) rejeitaria
    # junto 4 dos 13 in-scope, trocando um falso reprovacao por uma perda
    # real de recall. O limiar 0.7 antigo nao media "rejeicao": media
    # similaridade de cosseno, que nao e a mesma coisa.
    #
    # O sinal que separa e' o cross-encoder reranqueado
    # (`rerank_score_calibrated`, probabilidade DA-42): in-scope >= 0.975,
    # out-of-scope <= 0.050. E' o mesmo numero que decide a admissao na
    # pipeline (`_evidence_admission_score`) e na escalation, entao o gate
    # passa a medir o comportamento real em vez de um proxy que nao
    # discrimina.
    #
    # O threshold 0.5 e' o ponto neutro da sigmoid (sigma(0) = 0.50,
    # documentado em `retriever._sigmoid_calibrate`), nao um numero
    # ajustado para fazer o gate passar: ele fica a 0.45 do pior in-scope e a
    # 0.45 do pior out-of-scope, ou seja, no meio da maior folga que existe
    # entre os dois grupos. Um gate calibrado em cima do BERT precisao de
    # re-medir a cada troca de reranker; o ponto neutro nao.
    OOS_ADMISSION_MAX = 0.5
    print(f"\nOut-of-scope ({len(out_of_scope)} casos):")
    oos_ok = 0
    for case in out_of_scope:
        hits = retrieve(case["query"], target="incidents", top_k=1)
        # Ausencia de hit e' rejeicao legitima; hit sem o campo calibrado NAO
        # e' (significa que o rerank nao rodou, e tratar como 0.0 seria um
        # falso verde — a liacao 2 de QUALITY_GATES.md).
        if not hits:
            admission = 0.0
        else:
            admission = hits[0].get("rerank_score_calibrated")
            if admission is None:
                raise RuntimeError(
                    f"out-of-scope '{case['query'][:50]}': hit sem "
                    f"rerank_score_calibrated; o gate nao pode medir rejeicao "
                    f"neste estado (falso verde)"
                )
        ok = admission < OOS_ADMISSION_MAX
        oos_ok += int(ok)
        status = "✅" if ok else "❌"
        print(
            f"  {status} admissao={admission:.3f} "
            f"cosseno={hits[0]['score'] if hits else 0.0:.3f} "
            f"query='{case['query'][:50]}'"
        )

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
