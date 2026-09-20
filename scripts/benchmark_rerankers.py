"""Benchmark cientifico de modelos de reranking (cross-encoder) para o
dominio deste projeto: incidentes de integracao SAP, descritos em
PT-BR, com jargao tecnico em EN (IDoc, iFlow, RFC, OData, gateway...).

Motivacao (item do backlog da segunda revisao arquitetural externa,
tratado por ultimo, por ser um artefato de AVALIACAO cientifica, nao
uma mudanca de arquitetura): o reranker de producao
(`cross-encoder/ms-marco-MiniLM-L-6-v2`, ver app/rag/retriever.py) foi
escolhido por ser o default mais comum da comunidade LangChain/Qdrant,
nunca comparado formalmente contra alternativas - inclusive
alternativas MULTILINGUES, o que importa aqui porque as queries reais
sao majoritariamente em portugues, enquanto ms-marco-MiniLM-L-6-v2 foi
treinado so em ingles (MS MARCO).

Metodologia:
- Corpus: os mesmos `data/sample_docs/*.md` usados em producao (via
  fallback_dir do TARGETS["incidents"] em app/rag/ingest.py),
  CHUNKED com os MESMOS parametros de producao (MarkdownTextSplitter,
  chunk_size=500, chunk_overlap=50) - nao um corpus sintetico.
- Queries + relevancia: `data/eval/rag_eval_dataset.json` (13 casos
  in-scope, excluindo os 2 out_of_scope que nao tem documento
  relevante - reranking so faz sentido comparar quando ha uma resposta
  certa).
- Cada modelo reranqueia o CORPUS INTEIRO (nao um pool pre-filtrado
  por um primeiro estagio) para cada query - isola a qualidade do
  reranker em si, sem a variavel do retriever hibrido (Qdrant real nao
  esta disponivel neste ambiente - ver docs/ARCHITECTURE.md, mesma
  limitacao ja documentada para Neo4j/Docker). Multiplos chunks do
  mesmo documento sao deduplicados mantendo o de maior score - mesmo
  principio de agregacao por documento usado implicitamente em
  DiagnosisResponse.matched_source.
- Metricas: Hit@1, Recall@5, MRR@5, nDCG@5 (logica pura e testada em
  app/rag/eval_metrics.py / tests/test_eval_metrics.py) + latencia
  media por query (ms) + delta de RSS do processo apos carregar o
  modelo (proxy de uso de RAM, via psutil) + numero de parametros do
  modelo (proxy de custo computacional/CPU, mais estavel entre
  ambientes que uma medida de %CPU de uma chamada sincrona
  single-thread).

Uso:
    uv run python scripts/benchmark_rerankers.py
    uv run python scripts/benchmark_rerankers.py --models ms-marco-L6,ms-marco-L12

Cada modelo e baixado do Hugging Face Hub na primeira execucao (requer
rede) e o cache e limpo apos cada modelo ser avaliado, para nao
acumular varios GB em disco - este ambiente de desenvolvimento tem
orcamento de disco limitado (ver nota de RECURSOS abaixo).

NOTA DE RECURSOS (honestidade sobre o ambiente onde isto rodou): a
maquina usada para gerar os resultados documentados em
docs/RERANKER_BENCHMARK.md tem 2 vCPUs, ~3.8GB RAM e ~3.7GB de disco
livre no momento da execucao - sem GPU. Os modelos candidatos foram
escolhidos para caber nesse orcamento (nenhum acima de ~1.2GB em
disco). Um modelo maior (ex: BAAI/bge-reranker-v2-m3, ~2.2GB) poderia
ter melhor qualidade multilingue e fica registrado como proximo passo
natural, nao testado aqui por risco de OOM/disco cheio nesta maquina -
nao por decisao de que nao valeria a pena.
"""

from __future__ import annotations

import argparse
import gc
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

# app/ ja e importavel diretamente (instalado em modo editavel via
# `uv sync` - ver [tool.hatch.build.targets.wheel] em pyproject.toml),
# sem precisar de sys.path.insert - diferente de scripts/eval_rag.py
# (script mais antigo, mantido como esta por nao ser escopo desta
# mudanca).
from app.rag.eval_metrics import hit_at_1, mrr_at_k, ndcg_at_k, recall_at_k

BASE_DIR = Path(__file__).resolve().parents[1]
SAMPLE_DOCS_DIR = BASE_DIR / "data" / "sample_docs"
EVAL_DATASET_PATH = BASE_DIR / "data" / "eval" / "rag_eval_dataset.json"
RESULTS_JSON_PATH = BASE_DIR / "data" / "eval" / "reranker_benchmark_results.json"

# Mesmos parametros de chunking da collection "incidents" em producao
# (app/rag/ingest.py::TARGETS["incidents"]) - o benchmark tem que
# refletir o corpus que o reranker realmente ve, nao um corpus
# sintetico/simplificado.
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

TOP_K = 5  # Recall@5 / MRR@5 / nDCG@5, conforme pedido pela revisao externa

CANDIDATE_MODELS: dict[str, dict] = {
    "ms-marco-L6": {
        "hf_id": "cross-encoder/ms-marco-MiniLM-L-6-v2",
        "notes": "Baseline atual de producao (app/rag/retriever.py::RERANKER_MODEL). Ingles apenas (MS MARCO).",
    },
    "ms-marco-L12": {
        "hf_id": "cross-encoder/ms-marco-MiniLM-L-12-v2",
        "notes": "Mesma familia do baseline, mais profundo (12 camadas vs 6). Ingles apenas.",
    },
    "mmarco-mMiniLMv2": {
        "hf_id": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
        "notes": "Treinado no mMARCO (MS MARCO traduzido, incluindo portugues) - candidato multilingue leve.",
    },
    "bge-reranker-base": {
        "hf_id": "BAAI/bge-reranker-base",
        "notes": "Multilingue (100+ idiomas, incluindo PT), maior que os demais candidatos (~1.1GB).",
    },
}


@dataclass
class ModelResult:
    key: str
    hf_id: str
    notes: str
    ok: bool
    error: str | None = None
    hit_at_1: float = 0.0
    recall_at_5: float = 0.0
    mrr_at_5: float = 0.0
    ndcg_at_5: float = 0.0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    rss_delta_mb: float = 0.0
    param_count: int | None = None
    per_query: list[dict] = field(default_factory=list)


def _load_corpus() -> list[dict]:
    """Carrega e chunka data/sample_docs/*.md com os mesmos parametros
    de producao. Retorna uma lista de {"source": ..., "text": ...} -
    um item por chunk (varios chunks podem compartilhar o mesmo
    source)."""
    from langchain_text_splitters import MarkdownTextSplitter

    splitter = MarkdownTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    corpus = []
    for path in sorted(SAMPLE_DOCS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for chunk in splitter.split_text(text):
            corpus.append({"source": path.name, "text": chunk})
    return corpus


def _load_eval_cases() -> list[dict]:
    dataset = json.loads(EVAL_DATASET_PATH.read_text(encoding="utf-8"))
    return [c for c in dataset if c["difficulty"] != "out_of_scope"]


def _rank_documents(model, query: str, corpus: list[dict]) -> tuple[list[str], float]:
    """Reranqueia o corpus inteiro para uma query, deduplica por
    documento (mantendo o chunk de maior score) e retorna a lista de
    sources ordenada + a latencia da chamada predict() em ms."""
    pairs = [(query, chunk["text"]) for chunk in corpus]
    start = time.perf_counter()
    scores = model.predict(pairs)
    latency_ms = (time.perf_counter() - start) * 1000

    best_per_source: dict[str, float] = {}
    for chunk, score in zip(corpus, scores, strict=True):
        source = chunk["source"]
        if source not in best_per_source or score > best_per_source[source]:
            best_per_source[source] = float(score)

    ranked = sorted(best_per_source, key=lambda s: best_per_source[s], reverse=True)
    return ranked, latency_ms


def _benchmark_model(key: str, spec: dict, corpus: list[dict], cases: list[dict]) -> ModelResult:
    import psutil
    from sentence_transformers import CrossEncoder

    process = psutil.Process()
    rss_before_mb = process.memory_info().rss / (1024 * 1024)

    model = CrossEncoder(spec["hf_id"])
    rss_after_mb = process.memory_info().rss / (1024 * 1024)

    param_count = None
    try:
        param_count = sum(p.numel() for p in model.model.parameters())
    except Exception as exc:  # noqa: BLE001 - metrica auxiliar, nunca deve derrubar o benchmark
        print(f"  (nao foi possivel contar parametros: {exc})")

    hit1_total = recall_total = mrr_total = ndcg_total = 0.0
    latencies_ms: list[float] = []
    per_query: list[dict] = []

    for case in cases:
        query = case["query"]
        expected = set(case["expected_sources"])
        ranked, latency_ms = _rank_documents(model, query, corpus)
        latencies_ms.append(latency_ms)

        h1 = hit_at_1(ranked, expected)
        rec5 = recall_at_k(ranked, expected, k=TOP_K)
        mrr5 = mrr_at_k(ranked, expected, k=TOP_K)
        ndcg5 = ndcg_at_k(ranked, expected, k=TOP_K)

        hit1_total += h1
        recall_total += rec5
        mrr_total += mrr5
        ndcg_total += ndcg5

        per_query.append(
            {
                "query": query,
                "difficulty": case["difficulty"],
                "expected_sources": sorted(expected),
                "top5": ranked[:5],
                "hit_at_1": h1,
                "recall_at_5": rec5,
                "mrr_at_5": mrr5,
                "ndcg_at_5": ndcg5,
                "latency_ms": round(latency_ms, 1),
            }
        )

    n = len(cases)
    latencies_ms.sort()
    p95_index = max(0, round(0.95 * (len(latencies_ms) - 1)))

    result = ModelResult(
        key=key,
        hf_id=spec["hf_id"],
        notes=spec["notes"],
        ok=True,
        hit_at_1=hit1_total / n,
        recall_at_5=recall_total / n,
        mrr_at_5=mrr_total / n,
        ndcg_at_5=ndcg_total / n,
        avg_latency_ms=sum(latencies_ms) / n,
        p95_latency_ms=latencies_ms[p95_index],
        rss_delta_mb=rss_after_mb - rss_before_mb,
        param_count=param_count,
        per_query=per_query,
    )

    del model
    gc.collect()
    return result


def _clear_hf_cache_for(hf_id: str) -> None:
    """Remove do cache local do Hugging Face Hub o snapshot do modelo
    recem-avaliado - este ambiente tem orcamento de disco limitado
    (ver docstring do modulo), entao baixar 4 modelos sem limpar
    estouraria o espaco disponivel. Silencioso se o cache nao existir
    (ex: HF_HOME customizado) - limpeza e so uma otimizacao de disco,
    nunca deve derrubar o benchmark."""
    from huggingface_hub import scan_cache_dir

    try:
        cache_info = scan_cache_dir()
    except Exception:  # noqa: BLE001
        return
    repo_id = hf_id
    for repo in cache_info.repos:
        if repo.repo_id == repo_id:
            shutil.rmtree(repo.repo_path, ignore_errors=True)


def run_benchmark(model_keys: list[str] | None = None) -> list[ModelResult]:
    keys = model_keys or list(CANDIDATE_MODELS.keys())
    corpus = _load_corpus()
    cases = _load_eval_cases()
    print(f"Corpus: {len(corpus)} chunks de {len(list(SAMPLE_DOCS_DIR.glob('*.md')))} documentos")
    print(f"Casos de avaliacao (in-scope): {len(cases)}\n")

    results: list[ModelResult] = []
    for key in keys:
        spec = CANDIDATE_MODELS[key]
        print(f"--- {key} ({spec['hf_id']}) ---")
        try:
            result = _benchmark_model(key, spec, corpus, cases)
            print(
                f"Hit@1={result.hit_at_1:.2f} Recall@5={result.recall_at_5:.2f} "
                f"MRR@5={result.mrr_at_5:.2f} nDCG@5={result.ndcg_at_5:.2f} "
                f"latencia_media={result.avg_latency_ms:.0f}ms RSS+{result.rss_delta_mb:.0f}MB"
            )
        except Exception as exc:  # noqa: BLE001 - um modelo falhar (OOM, download) nao pode derrubar os demais
            result = ModelResult(
                key=key, hf_id=spec["hf_id"], notes=spec["notes"], ok=False, error=str(exc)
            )
            print(f"FALHOU: {exc}")
        results.append(result)
        _clear_hf_cache_for(spec["hf_id"])
        print()

    return results


def _print_summary_table(results: list[ModelResult]) -> None:
    print("=" * 100)
    header = f"{'Modelo':<20}{'Hit@1':<9}{'Recall@5':<11}{'MRR@5':<9}{'nDCG@5':<9}{'Lat.media':<12}{'RSS':<10}"
    print(header)
    print("-" * 100)
    for r in results:
        if not r.ok:
            print(f"{r.key:<20}FALHOU: {r.error}")
            continue
        print(
            f"{r.key:<20}{r.hit_at_1:<9.2f}{r.recall_at_5:<11.2f}{r.mrr_at_5:<9.2f}"
            f"{r.ndcg_at_5:<9.2f}{r.avg_latency_ms:<9.0f}ms  {r.rss_delta_mb:<7.0f}MB"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models",
        type=str,
        default=None,
        help=f"Lista separada por virgula, dentre: {', '.join(CANDIDATE_MODELS)}. Default: todos.",
    )
    args = parser.parse_args()
    model_keys = args.models.split(",") if args.models else None
    if model_keys:
        unknown = set(model_keys) - set(CANDIDATE_MODELS)
        if unknown:
            parser.error(f"Modelo(s) desconhecido(s): {unknown}. Validos: {list(CANDIDATE_MODELS)}")

    results = run_benchmark(model_keys)
    _print_summary_table(results)

    RESULTS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_JSON_PATH.write_text(
        json.dumps([r.__dict__ for r in results], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nResultados completos salvos em {RESULTS_JSON_PATH.relative_to(BASE_DIR)}")


if __name__ == "__main__":
    main()
