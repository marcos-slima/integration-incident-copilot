"""Quality gate deterministico da DA-51.

Roda verificacoes que NAO precisam de LLM, Qdrant ou Ollama: integridade
dos datasets de avaliacao, cobertura do corpus, invariante do reranker
medido (DA-29), validade das configs do promptfoo, existencia de baseline
de LLM e coerencia da lista de DAs candidatas no CLAUDE.md.

Exit code 0 = tudo passou (ou apenas avisos, salvo --strict).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.evaluation.gates import (
    DEFAULT_THRESHOLDS,
    GATES,
    PROMPTFOO_BASELINE,
    Thresholds,
    compare_promptfoo,
    normalize_promptfoo_results,
    run_all,
    run_one,
)

SYMBOLS = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}


def _print_report(findings: list[dict[str, str]]) -> None:
    width = max((len(f["check"]) for f in findings), default=10)
    for finding in findings:
        print(f"[{SYMBOLS[finding['severity']]}] {finding['check']:<{width}}  {finding['message']}")


def _write_baseline(results_path: Path, destination: Path) -> int:
    payload = json.loads(results_path.read_text(encoding="utf-8"))
    cases = normalize_promptfoo_results(payload)
    baseline = {
        "generated_at": None,
        "model": None,
        "cases": cases,
        "pass_rate": round(sum(cases.values()) / len(cases), 4) if cases else 0.0,
    }
    destination.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"baseline gravado em {destination} ({sum(cases.values())}/{len(cases)} casos passando)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Quality gate deterministico (DA-51)")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="raiz do repositorio")
    parser.add_argument("--json", type=Path, help="grava o relatorio em JSON (artefato do CI)")
    parser.add_argument("--strict", action="store_true", help="promove aviso a falha")
    parser.add_argument("--only", choices=sorted(GATES), help="roda apenas um gate")
    parser.add_argument("--min-rag-cases", type=int, default=DEFAULT_THRESHOLDS.min_rag_cases)
    parser.add_argument("--min-hit-at-1", type=float, default=DEFAULT_THRESHOLDS.min_hit_at_1)
    parser.add_argument(
        "--min-benchmark-margin", type=float, default=DEFAULT_THRESHOLDS.min_benchmark_margin
    )
    parser.add_argument(
        "--compare-promptfoo",
        type=Path,
        help="compara um resultado do promptfoo com o baseline versionado",
    )
    parser.add_argument(
        "--write-promptfoo-baseline",
        type=Path,
        help="grava o baseline de LLM a partir de um resultado do promptfoo",
    )
    args = parser.parse_args(argv)

    root: Path = args.root.resolve()
    if args.write_promptfoo_baseline:
        return _write_baseline(args.write_promptfoo_baseline, root / PROMPTFOO_BASELINE)

    if args.compare_promptfoo:
        baseline_path = root / PROMPTFOO_BASELINE
        if not baseline_path.exists():
            print(f"[FAIL] baseline ausente: {baseline_path}", file=sys.stderr)
            return 1
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8")).get("cases", {})
            current = normalize_promptfoo_results(
                json.loads(args.compare_promptfoo.read_text(encoding="utf-8"))
            )
        except (ValueError, json.JSONDecodeError, AttributeError) as exc:
            print(f"[FAIL] resultado do promptfoo ilegivel: {exc}", file=sys.stderr)
            return 1
        findings = compare_promptfoo(current, baseline)
    else:
        thresholds = Thresholds(
            min_rag_cases=args.min_rag_cases,
            min_hit_at_1=args.min_hit_at_1,
            min_benchmark_margin=args.min_benchmark_margin,
        )
        findings = run_one(args.only, root, thresholds) if args.only else run_all(root, thresholds)

    payload = [f.to_dict() for f in findings]
    _print_report(payload)

    failures = [f for f in findings if f.is_failure or (args.strict and f.severity == "warn")]
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {"findings": payload, "failures": len(failures)}, ensure_ascii=False, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"relatorio JSON: {args.json}")

    if failures:
        print(f"\n{len(failures)} gate(s) falharam", file=sys.stderr)
        return 1
    print("\nquality gate: tudo passou")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
