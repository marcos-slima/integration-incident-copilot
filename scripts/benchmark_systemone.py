"""Benchmark da API System One (Ollama >= 0.35) contra a referencia
DETERMINISTICA de classificacao de dominio.

O QUE ESTE SCRIPT MEDE (e o que ele NAO mede)

Este benchmark nao usa o LLM para se avaliar: a referencia e
`app/agent/supervisor.py::classify_domain`, funcao pura de
`interface_type` + palavras-chave (DA-22, invariante 6). Isso e
deliberado por dois motivos:

1. GROUND TRUTH SEM LABEL INVENTADO. Nao existe dataset rotulado de
   "incidente -> dominio" neste repositorio. Derivando a expectativa
   do proprio codigo determinista, nao ha label para envelhecer: se
   `classify_domain` mudar, o benchmark mede contra a regra nova em
   vez de expor um JSON de labels desatualizado. Os casos
   adversariais abaixo sao a excecao explicita - tem expectativa
   AUTORADA, com a regra que a fundamenta registrada em `why`.

2. O que esta em jogo NAO e "o modelo sabe classificar?", e "o
   modelo concorda com o que o codigo ja decide gratis?". Se a
   concordancia for alta, o modelo e redundante. Se for baixa, o
   merged de divergencias mostra ONDE a heuristica e fragil - que e o
   unico achado acionavel.

Severidade (tarefa `score`) NAO tem expectativa: nao existe rotulo de
gravidade em lugar nenhum do repositorio, e inventar um seria
fabricar a mesma metrica que a DA-16 combate. Ela e medida so por
distribuicao, confianca e determinismo.

O QUE ESTE SCRIPT NAO AUTORIZA

Um numero de concordancia alto NAO torna `classify_domain`
substituivel. `classify_domain` e instantaneo, auditavel e coberto
por teste; o System One e uma chamada de rede com modelo carregado.
Evencer mede a qualidade de um SINAL AUXILIAR (propor, abster),
nao autoriza mover decisao estrutural para o modelo - mesma logica
da invariante 3 (guardrails em codigo, nao em prompt).

SCHEMA (nao documentado pelo projeto)

O contrato de `/v1/systemone` nao esta documentado no upstream e foi
extraido do binario `ollama` por inspecao de strings das tags
`json:"..."` do pacote `decision`:

    POST /v1/systemone
      model     string            obrigatorio; o binario exige modelo
                                 Nimble ou Tev LOCAL
      state     string|obj|array  o texto a classificar
      questions map[string]object 1..64 perguntas; a CHAVE e o id
                                 devolvido em `answers`
        { type      "noul"|"choice"|"score"
          instructions string
          criteria  noul   -> {true: desc, false: desc}
                    choice -> {option: desc, ...}
                    score  -> [desc da faixa 0, faixa 1, ...] }

    -> { answers: { id: {type, noul|choice|score,
                         probabilities: {...}, confidence: float} },
         usage: {input_tokens, output_tokens} }

`criteria` para `noul`/`choice` e objeto e para `score` e ARRAY, e a
distincao e validada pelo proprio binario ("noul criteria must be an
object of true/false descriptions", "choice criteria must map option
keys to descriptions or null", "score criteria must be an array of
descriptions").

Uso:
    uv run python scripts/benchmark_systemone.py
    uv run python scripts/benchmark_systemone.py --repeat 3
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from itertools import pairwise
from pathlib import Path

# app/ e importavel diretamente (instalado em modo editavel via
# `uv sync`), igual a scripts/benchmark_rerankers.py.
from app.agent.supervisor import classify_domain

BASE_DIR = Path(__file__).resolve().parents[1]
SAMPLE_DOCS_DIR = BASE_DIR / "data" / "sample_docs"
RESULTS_JSON_PATH = BASE_DIR / "data" / "eval" / "systemone_benchmark_results.json"

OLLAMA_BASE_URL = "http://localhost:11434"
SYSTEMONE_PATH = "/v1/systemone"
DEFAULT_MODEL = "tev1"

CANDIDATE_MODELS: dict[str, dict] = {
    "tev1": {
        "model": "tev1",
        "notes": "Tev1 local instalado nesta maquina. Variante exata (0.8B/4B) nao "
        "exposta por /api/tags.",
    },
    "nimble": {
        "model": "nimble",
        "notes": "Nimble 9B. NAO instalado localmente - mantido como slot para quando "
        "rodar em maquina que tenha o model.",
    },
}

DOMAIN_OPTIONS: dict[str, str] = {
    "sap": "produto SAP on-premise ou middleware SAP: S/4HANA, ECC, IDoc, RFC, "
    "BAPI, OData gateway, CAP, PO/PI, iFlow/Integration Suite",
    "saas": "SaaS multi-inquilino: Ariba, SuccessFactors, ServiceNow, Salesforce, "
    "Workday, SuccessFactors Employee Central",
    "generic": "nenhum produto identificavel no texto; caso sem sinal de dominio",
}

SEVERITY_BANDS: list[str] = [
    "impacto trivial ou apenas informativo; processo continua rodando",
    "atraso ou erro visivel ao usuario, mas existe contorno manual",
    "processo de negocio bloqueado sem contorno; operacao travada",
]

# Casos adversariais com expectativa AUTORADA (nao derivada de
# `classify_domain`) - sao os casos de fronteira que a DA-22 §3.5
# corrigiu. O campo `why` registra a regra para que a expectativa nao
# vire dogma sem explicacao.
AUTHORED_CASES: list[dict] = [
    {
        "id": "wordboundary_sapato",
        "text": "O sapato do usuario ficou manchado depois de usar o portal. "
        "Nao ha integracao envolvida.",
        "expected": "generic",
        "why": "DA-22 §3.5: 'sap' com word boundary para nao casar com 'sapato'.",
    },
    {
        "id": "wordboundary_sapiens",
        "text": "Os dados do cliente Sapiens foram duplicados na carga manual.",
        "expected": "generic",
        "why": "DA-22 §3.5: 'sap' como substring disparava falso positivo em 'sapiens'.",
    },
    {
        "id": "no_signal",
        "text": "O relatorio mensal nao abriu na manha de segunda-feira.",
        "expected": "generic",
        "why": "Sem interface_type e sem keyword SAP, `classify_domain` cai em "
        "'generic' por definicao.",
    },
    {
        "id": "idoc_only",
        "text": "IDoc travado com status 51 na fila de entrada (WE02/BD87).",
        "expected": "sap",
        "why": "'idoc' em _SAP_KEYWORDS - evidencia de middleware SAP, nao de SaaS.",
    },
    {
        "id": "po_pi_only",
        "text": "Mensagem presa no Process Integration; diretorio de erros do PO "
        "crescendo sem consumo.",
        "expected": "sap",
        "why": "DA-56: 'process integration' em _SAP_KEYWORDS; PO/PI e middleware SAP, nao SaaS.",
    },
    {
        "id": "saas_only",
        "text": "Worker nao foi criado no Workday por divergencia de "
        "Person_ID_External vindo do SuccessFactors.",
        "expected": "saas",
        "why": "Sem interface_type, 'successfactors' em _SAP_KEYWORDS leva a 'sap' "
        "pelo fallback - este caso expoe a ambiguidade Ariba/SuccessFactors que a "
        "DA-22 documenta (em interface_type explicito seriam 'saas').",
    },
]


@dataclass
class CaseResult:
    id: str
    text_preview: str
    expected: str
    predicted: str | None
    confidence: float | None
    probabilities: dict[str, float] | None
    correct: bool | None
    provenance: str


@dataclass
class ModelResult:
    key: str
    model: str
    notes: str
    ok: bool
    error: str | None = None
    n_cases: int = 0
    agreement: float | None = None
    disagreements: list[dict] = field(default_factory=list)
    abstention_curve: list[dict] = field(default_factory=list)
    confidence_bins: list[dict] = field(default_factory=list)
    severity_distribution: dict[str, float] = field(default_factory=dict)
    severity_mean_confidence: float | None = None
    determinism_identical_fraction: float | None = None
    determinism_n: int = 0
    latency_ms_mean: float = 0.0
    latency_ms_p50: float = 0.0
    latency_ms_p95: float = 0.0
    input_tokens_mean: float = 0.0
    output_tokens_mean: float = 0.0
    per_case: list[CaseResult] = field(default_factory=list)


def _load_corpus() -> list[dict]:
    """Casos derivados de data/sample_docs/*.md.

    A expectativa vem de `classify_domain` - o texto do documento entra
    como `description`, que e exatamente a entrada do fallback por
    palavra-chave (o caminho que roda quando `interface_type` nao vem
    preenchido na requisicao).
    """
    cases: list[dict] = []
    for path in sorted(SAMPLE_DOCS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        cases.append(
            {
                "id": path.stem,
                "text": text,
                "expected": classify_domain({"description": text}),
                "provenance": "derivado:classify_domain",
            }
        )
    for authored in AUTHORED_CASES:
        cases.append({**authored, "provenance": "autorado"})
    return cases


def _build_payload(model: str, text: str) -> dict:
    return {
        "model": model,
        "state": text,
        "questions": {
            "dominio": {
                "type": "choice",
                "instructions": "Classifique o dominio do incidente de integracao.",
                "criteria": DOMAIN_OPTIONS,
            },
            "severidade": {
                "type": "score",
                "instructions": "Avalie a gravidade operacional do incidente.",
                "criteria": SEVERITY_BANDS,
            },
        },
    }


def _call_systemone(payload: dict, timeout: float) -> dict:
    """Chamada via stdlib para manter o script sem dependencia de rede
    alem do proprio Ollama (httpx seria dependencia nova de um script
    de medicao)."""
    request = urllib.request.Request(
        f"{OLLAMA_BASE_URL}{SYSTEMONE_PATH}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _abstention_curve(results: list[CaseResult], thresholds: list[float]) -> list[dict]:
    """Precision-at-coverage: para cada limiar de confianca, que
    fração dos casos ainda respondemos e, entre os respondidos, quantos
    concordam com a referencia.

    E a curva que responde a pergunta operacional ("qual threshold
    torna 'propor' seguro?"). Um limiar alto demais responde pouco e
    nao agrega; baixo demais responde sempre e concorda pouco - e o
    pior caso, porque parece util.
    """
    scored = [r for r in results if r.confidence is not None and r.correct is not None]
    if not scored:
        return []
    curve: list[dict] = []
    for threshold in thresholds:
        answered = [r for r in scored if r.confidence >= threshold]
        if not answered:
            continue
        correct = sum(1 for r in answered if r.correct)
        curve.append(
            {
                "threshold": round(threshold, 4),
                "coverage": len(answered) / len(scored),
                "precision": correct / len(answered),
                "n_answered": len(answered),
            }
        )
    return curve


def _confidence_bins(results: list[CaseResult], edges: list[float]) -> list[dict]:
    """Acerto por faixa de confianca - a curva de calibracao."""
    scored = [r for r in results if r.confidence is not None and r.correct is not None]
    bins: list[dict] = []
    for low, high in pairwise(edges):
        bucket = [r for r in scored if low <= (r.confidence or 0.0) < high]
        if not bucket:
            continue
        bins.append(
            {
                "range": f"[{low:.1f},{high:.1f})",
                "n": len(bucket),
                "accuracy": sum(1 for r in bucket if r.correct) / len(bucket),
            }
        )
    return bins


def _benchmark_model(key: str, spec: dict, cases: list[dict], repeat: int, timeout: float):
    result = ModelResult(key=key, model=spec["model"], notes=spec["notes"], ok=False)
    latencies: list[float] = []
    input_tokens: list[float] = []
    output_tokens: list[float] = []
    severity_scores: list[float] = []
    severity_confidences: list[float] = []
    base_fingerprint: str | None = None
    repeat_fingerprints: list[str] = []

    for case in cases:
        payload = _build_payload(spec["model"], case["text"])
        started = time.perf_counter()
        try:
            response = _call_systemone(payload, timeout)
        except urllib.error.HTTPError as exc:
            result.error = f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}"
            return result
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            return result
        latencies.append((time.perf_counter() - started) * 1000.0)

        usage = response.get("usage", {}) or {}
        input_tokens.append(float(usage.get("input_tokens", 0) or 0))
        output_tokens.append(float(usage.get("output_tokens", 0) or 0))

        answers = response.get("answers", {}) or {}
        domain = answers.get("dominio", {}) or {}
        severity = answers.get("severidade", {}) or {}
        predicted = domain.get("choice")
        confidence = domain.get("confidence")
        probabilities = domain.get("probabilities")

        # Sem `probabilities` nao ha como calibrar nemreshold; o
        # benchmark registra isso em vez de assumir.
        correct = predicted == case["expected"]
        if predicted is None:
            confidence = None

        result.per_case.append(
            CaseResult(
                id=case["id"],
                text_preview=case["text"][:110].replace("\n", " "),
                expected=case["expected"],
                predicted=predicted,
                confidence=confidence,
                probabilities=probabilities,
                correct=correct,
                provenance=case["provenance"],
            )
        )
        if not correct:
            result.disagreements.append(
                {
                    "id": case["id"],
                    "expected": case["expected"],
                    "predicted": predicted,
                    "confidence": confidence,
                    "provenance": case["provenance"],
                }
            )

        if isinstance(severity.get("score"), int | float):
            severity_scores.append(float(severity["score"]))
        if isinstance(severity.get("confidence"), int | float):
            severity_confidences.append(float(severity["confidence"]))

        fingerprint = json.dumps(
            {k: answers.get(k) for k in sorted(answers)}, sort_keys=True, ensure_ascii=False
        )
        if base_fingerprint is None:
            base_fingerprint = fingerprint

    if result.per_case and repeat > 1:
        # Repeticao: mesma pergunta, mesma carga de estado, para medir
        # se o System One e reprodutivel (DA-2 exige determinismo para
        # qualquer coisa que entre em caminho medido).
        for _ in range(max(0, repeat - 1)):
            payload = _build_payload(spec["model"], cases[0]["text"])
            try:
                again = _call_systemone(payload, timeout)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                break
            answers = again.get("answers", {}) or {}
            repeat_fingerprints.append(
                json.dumps(
                    {k: answers.get(k) for k in sorted(answers)},
                    sort_keys=True,
                    ensure_ascii=False,
                )
            )
        # Compara SO as repeticoes com a resposta original do caso-base.
        # Incluir os outros 20 casos aqui daria uma metrica sem sentido
        # (textos diferentes produzem respostas diferentes por definicao).
        comparable = [base_fingerprint, *repeat_fingerprints]
        result.determinism_identical_fraction = comparable.count(comparable[0]) / len(comparable)
        result.determinism_n = len(comparable)

    result.ok = True
    result.n_cases = len(result.per_case)
    scored = [r for r in result.per_case if r.correct is not None]
    result.agreement = (sum(1 for r in scored if r.correct) / len(scored)) if scored else None
    result.abstention_curve = _abstention_curve(result.per_case, [0.0, 0.1, 0.25, 0.5, 0.75, 0.9])
    result.confidence_bins = _confidence_bins(
        result.per_case, [0.0, 0.1, 0.2, 0.4, 0.6, 0.8, 1.0001]
    )
    if severity_scores:
        bands = {str(i): 0 for i in range(len(SEVERITY_BANDS))}
        for score in severity_scores:
            index = min(int(score), len(SEVERITY_BANDS) - 1)
            bands[str(index)] += 1
        total = len(severity_scores)
        result.severity_distribution = {k: v / total for k, v in bands.items()}
    result.severity_mean_confidence = (
        statistics.fmean(severity_confidences) if severity_confidences else None
    )
    result.latency_ms_mean = statistics.fmean(latencies) if latencies else 0.0
    result.latency_ms_p50 = statistics.median(latencies) if latencies else 0.0
    if latencies:
        ordered = sorted(latencies)
        index = max(0, round(0.95 * len(ordered)) - 1)
        result.latency_ms_p95 = ordered[index]
    result.input_tokens_mean = statistics.fmean(input_tokens) if input_tokens else 0.0
    result.output_tokens_mean = statistics.fmean(output_tokens) if output_tokens else 0.0
    return result


def _print_summary_table(results: list[ModelResult]) -> None:
    header = f"{'model':<10} {'ok':<6} {'n':<4} {'acordo':<9} {'p50 ms':<9} {'p95 ms':<9} {'in_tok':<8} {'out_tok':<8} {'identico':<9}"
    print(header)
    print("-" * len(header))
    for result in results:
        if not result.ok:
            print(
                f"{result.key:<10} {'NO':<6} {'-':<4} {'-':<9} {'-':<9} {'-':<9} {'-':<8} {'-':<8} {'-':<9}"
            )
            print(f"    erro: {result.error}")
            continue
        agreement = f"{result.agreement:.3f}" if result.agreement is not None else "-"
        identical = (
            f"{result.determinism_identical_fraction:.2f}"
            if result.determinism_identical_fraction is not None
            else "-"
        )
        print(
            f"{result.key:<10} {'sim':<6} {result.n_cases:<4} {agreement:<9} "
            f"{result.latency_ms_p50:<9.0f} {result.latency_ms_p95:<9.0f} "
            f"{result.input_tokens_mean:<8.0f} {result.output_tokens_mean:<8.0f} {identical:<9}"
        )
        if result.disagreements:
            print(f"    divergencias ({len(result.disagreements)}):")
            for item in result.disagreements:
                print(
                    f"      {item['id']}: esperado={item['expected']} "
                    f"previsto={item['predicted']} conf={item['confidence']} "
                    f"[{item['provenance']}]"
                )
        if result.confidence_bins:
            print("    calibracao (acerto por faixa de confianca):")
            for entry in result.confidence_bins:
                print(
                    f"      {entry['range']:<14} n={entry['n']:<3} acerto={entry['accuracy']:.3f}"
                )
        if result.abstention_curve:
            print("    curva de abstencia (cobertura / precisao):")
            for entry in result.abstention_curve:
                print(
                    f"      conf>={entry['threshold']:<6} cobertura={entry['coverage']:.2f} "
                    f"precisao={entry['precision']:.3f} (n={entry['n_answered']})"
                )
        if result.severity_distribution:
            print(f"    severidade (sem ground truth): {result.severity_distribution}")
            print(f"    confianca media da severidade: {result.severity_mean_confidence}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models",
        type=str,
        default=None,
        help=f"Lista separada por virgula, dentre: {', '.join(CANDIDATE_MODELS)}. Default: tev1.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Repeticoes do mesmo caso para medir reprodutibilidade (default: 1).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Timeout por chamada, em segundos (default: 180).",
    )
    args = parser.parse_args()

    model_keys = [k.strip() for k in args.models.split(",")] if args.models else ["tev1"]
    unknown = set(model_keys) - set(CANDIDATE_MODELS)
    if unknown:
        parser.error(f"Modelo(s) desconhecido(s): {unknown}. Validos: {list(CANDIDATE_MODELS)}")

    cases = _load_corpus()
    print(f"Corpus: {len(cases)} casos ({len(AUTHORED_CASES)} autorados, resto derivado).")

    results = []
    for key in model_keys:
        print(f"\n=== {key} ===")
        results.append(
            _benchmark_model(key, CANDIDATE_MODELS[key], cases, args.repeat, args.timeout)
        )

    print()
    _print_summary_table(results)

    RESULTS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_JSON_PATH.write_text(
        json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nResultados completos salvos em {RESULTS_JSON_PATH.relative_to(BASE_DIR)}")


if __name__ == "__main__":
    main()
