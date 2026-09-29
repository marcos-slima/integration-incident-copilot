from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

PIPELINE_INTERFACE_TYPES = frozenset(
    {"odata", "rfc", "servicenow", "salesforce", "workday", "ariba", "cap", "apim"}
)
DIFFICULTIES = frozenset({"easy", "medium", "hard", "out_of_scope"})
REQUIRED_CASE_FIELDS = ("query", "expected_sources", "difficulty")

RAG_DATASET = Path("data/eval/rag_eval_dataset.json")
RERANKER_BENCHMARK = Path("data/eval/reranker_benchmark_results.json")
PROMPTFOO_BASELINE = Path("data/eval/promptfoo_baseline.json")
CORPUS_DIR = Path("data/sample_docs")
RERANKER_SOURCE = Path("app/rag/retriever.py")
ARCHITECTURE_DOC = Path("docs/ARCHITECTURE.md")
CLAUDE_DOC = Path("CLAUDE.md")


@dataclass(frozen=True)
class Thresholds:
    min_rag_cases: int = 15
    min_hit_at_1: float = 0.90
    min_benchmark_margin: float = 0.05
    min_promptfoo_cases: int = 1


DEFAULT_THRESHOLDS = Thresholds()


@dataclass(frozen=True)
class Finding:
    check: str
    severity: str
    message: str

    @property
    def is_failure(self) -> bool:
        return self.severity == "fail"

    def to_dict(self) -> dict[str, str]:
        return {"check": self.check, "severity": self.severity, "message": self.message}


def _ok(check: str) -> list[Finding]:
    return [Finding(check=check, severity="pass", message="ok")]


def _fail(check: str, message: str) -> list[Finding]:
    return [Finding(check=check, severity="fail", message=message)]


def _warn(check: str, message: str) -> list[Finding]:
    return [Finding(check=check, severity="warn", message=message)]


def _load_json(path: Path, check: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"{path} nao existe") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} nao e JSON valido: {exc}") from None


def load_rag_dataset(root: Path) -> list[dict[str, Any]]:
    data = _load_json(root / RAG_DATASET, "rag_dataset")
    if not isinstance(data, list):
        raise TypeError(f"{RAG_DATASET} deveria ser uma lista de casos")
    return data


def check_rag_dataset(
    root: Path = REPO_ROOT, thresholds: Thresholds = DEFAULT_THRESHOLDS
) -> list[Finding]:
    check = "rag_dataset_schema"
    try:
        cases = load_rag_dataset(root)
    except (ValueError, TypeError) as exc:
        return _fail(check, str(exc))

    problems: list[str] = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            problems.append(f"caso #{index} nao e objeto")
            continue
        for field_name in REQUIRED_CASE_FIELDS:
            if field_name not in case:
                problems.append(f"caso #{index} sem campo obrigatorio '{field_name}'")
        query = case.get("query")
        if not isinstance(query, str) or not query.strip():
            problems.append(f"caso #{index} com query vazia ou nao textual")
        sources = case.get("expected_sources")
        difficulty = case.get("difficulty")
        if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
            problems.append(f"caso #{index} com expected_sources invalido")
        elif difficulty == "out_of_scope":
            if sources:
                problems.append(
                    f"caso #{index} e out_of_scope mas espera {sources}; "
                    "o proprio caso se contradiz (o escopo e nao recuperar nada)"
                )
        elif not sources:
            problems.append(f"caso #{index} sem expected_sources")
        if difficulty not in DIFFICULTIES:
            problems.append(
                f"caso #{index} com difficulty '{difficulty}' fora de {sorted(DIFFICULTIES)}"
            )
        interface_type = case.get("interface_type")
        if interface_type is not None and interface_type not in PIPELINE_INTERFACE_TYPES:
            problems.append(
                f"caso #{index} com interface_type '{interface_type}' fora do Literal do pipeline"
            )

    if len(cases) < thresholds.min_rag_cases:
        problems.append(
            f"apenas {len(cases)} casos; piso e {thresholds.min_rag_cases} (remover casos enfraquece o gate)"
        )

    return _fail(check, "; ".join(problems)) if problems else _ok(check)


def check_corpus_coverage(root: Path = REPO_ROOT) -> list[Finding]:
    check = "corpus_coverage"
    try:
        cases = load_rag_dataset(root)
    except (ValueError, TypeError) as exc:
        return _fail(check, str(exc))

    corpus = {p.name for p in (root / CORPUS_DIR).rglob("*.md")}
    missing = sorted(
        {s for case in cases for s in case.get("expected_sources", []) if s not in corpus}
    )
    if missing:
        return _fail(check, f"expected_sources sem documento no corpus: {missing}")
    return _ok(check)


def check_difficulty_mix(root: Path = REPO_ROOT) -> list[Finding]:
    check = "dataset_difficulty_mix"
    try:
        cases = load_rag_dataset(root)
    except (ValueError, TypeError) as exc:
        return _fail(check, str(exc))

    present = {case.get("difficulty") for case in cases}
    absent = sorted(DIFFICULTIES - present)
    if absent:
        return _fail(check, f"sem nenhum caso de dificuldade {absent} (gate fica trivial sem eles)")
    return _ok(check)


def _parse_reranker_model(root: Path) -> str:
    source = (root / RERANKER_SOURCE).read_text(encoding="utf-8")
    match = re.search(r'RERANKER_MODEL\s*(?::[^=]+)?=\s*"([^"]+)"', source)
    if not match:
        raise ValueError("RERANKER_MODEL nao encontrado em app/rag/retriever.py")
    return match.group(1)


def check_reranker_invariant(
    root: Path = REPO_ROOT, thresholds: Thresholds = DEFAULT_THRESHOLDS
) -> list[Finding]:
    """DA-29: o modelo em producao tem que ser o vencedor medido, com margem sobre o baseline."""
    check = "reranker_invariant"
    try:
        in_code = _parse_reranker_model(root)
        results = _load_json(root / RERANKER_BENCHMARK, check)
    except (ValueError, TypeError) as exc:
        return _fail(check, str(exc))

    if not isinstance(results, list) or not results:
        return _fail(check, f"{RERANKER_BENCHMARK} sem resultados de benchmark")
    scored = [
        r for r in results if isinstance(r, dict) and isinstance(r.get("hit_at_1"), (int, float))
    ]
    if not scored:
        return _fail(check, f"{RERANKER_BENCHMARK} sem nenhum resultado com hit_at_1")

    winner = max(scored, key=lambda r: r["hit_at_1"])
    findings: list[Finding] = []

    if in_code != winner.get("hf_id"):
        findings.append(
            _fail(
                check,
                f"app/rag/retriever.py usa '{in_code}', mas o vencedor medido e "
                f"'{winner.get('hf_id')}' (hit@1 {winner['hit_at_1']:.3f})",
            )[0]
        )
    if winner["hit_at_1"] < thresholds.min_hit_at_1:
        findings.append(
            _fail(
                check,
                f"hit@1 do vencedor {winner['hit_at_1']:.3f} abaixo do piso {thresholds.min_hit_at_1}",
            )[0]
        )

    baselines = [r for r in scored if r is not winner and "marco" in str(r.get("key", "")).lower()]
    if not baselines:
        findings.append(
            _warn(check, "nenhum baseline ms-marco no benchmark; margem nao verificavel")[0]
        )
    else:
        baseline = max(baselines, key=lambda r: r["hit_at_1"])
        margin = winner["hit_at_1"] - baseline["hit_at_1"]
        if margin < thresholds.min_benchmark_margin:
            findings.append(
                _fail(
                    check,
                    f"margem de hit@1 sobre '{baseline.get('key')}' e {margin:+.3f}, "
                    f"abaixo do piso {thresholds.min_benchmark_margin:+.3f}",
                )[0]
            )
    return findings or _ok(check)


def check_promptfoo_configs(root: Path = REPO_ROOT) -> list[Finding]:
    check = "promptfoo_configs"
    configs = sorted(root.glob("promptfooconfig*.yaml"))
    if not configs:
        return _fail(check, "nenhum promptfooconfig*.yaml encontrado")

    try:
        import yaml
    except ImportError:
        return _fail(
            check, "PyYAML ausente; declara a dependencia explicitamente em pyproject.toml"
        )

    problems: list[str] = []
    for config_path in configs:
        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            problems.append(f"{config_path.name} nao e YAML valido: {exc}")
            continue
        if not isinstance(data, dict):
            problems.append(f"{config_path.name} nao e um mapeamento")
            continue
        for key in ("prompts", "tests", "providers"):
            if not data.get(key):
                problems.append(f"{config_path.name} sem '{key}'")
        tests = data.get("tests") or []
        if isinstance(tests, list) and len(tests) < 1:
            problems.append(f"{config_path.name} sem casos de teste")

        for provider in data.get("providers") or []:
            if not isinstance(provider, dict):
                continue
            provider_id = str(provider.get("id", ""))
            if provider_id.startswith("exec:"):
                for token in provider_id.split():
                    if token.endswith(".py") and not (root / token).exists():
                        problems.append(
                            f"{config_path.name} referencia script inexistente: {token}"
                        )

    return _fail(check, "; ".join(problems)) if problems else _ok(check)


def check_llm_baseline(root: Path = REPO_ROOT) -> list[Finding]:
    """O gate de LLM so fecha o ciclo com baseline versionado; sem ele, o job agendado nao tem com o que comparar."""
    check = "llm_baseline"
    baseline_path = root / PROMPTFOO_BASELINE
    if not baseline_path.exists():
        return _warn(
            check,
            f"{PROMPTFOO_BASELINE} ausente; rode localmente e grave com "
            "'python scripts/quality_gate.py --write-promptfoo-baseline <resultados.json>'",
        )
    try:
        data = _load_json(baseline_path, check)
    except (ValueError, TypeError) as exc:
        return _fail(check, str(exc))
    if not isinstance(data, dict) or not isinstance(data.get("cases"), dict):
        return _fail(check, f"{PROMPTFOO_BASELINE} sem mapa 'cases'")
    return _ok(check)


def _candidate_das(root: Path) -> list[str]:
    text = (root / CLAUDE_DOC).read_text(encoding="utf-8")
    block = re.search(r"DAs candidatas.*?\n\n", text, re.DOTALL)
    if not block:
        return []
    return sorted(set(re.findall(r"DA-\d+", block.group(0))))


def check_candidate_das(root: Path = REPO_ROOT) -> list[Finding]:
    """Lista de DAs candidatas no CLAUDE.md nao pode conter DA ja entregue (ACHADO: DA-32)."""
    check = "candidate_das_fresh"
    candidates = _candidate_das(root)
    if not candidates:
        return _warn(check, "nenhuma lista de DAs candidatas encontrada no CLAUDE.md")

    architecture = (root / ARCHITECTURE_DOC).read_text(encoding="utf-8")
    delivered = [
        da
        for da in candidates
        if re.search(rf"^#+\s.*\({da}\)", architecture, re.MULTILINE)
        or re.search(rf"entregue em {da}", architecture, re.IGNORECASE)
    ]
    if delivered:
        return _fail(
            check,
            f"{delivered} estao marcadas como candidatas no CLAUDE.md mas ja tem secao em docs/ARCHITECTURE.md",
        )
    return _ok(check)


GATES = {
    "rag_dataset_schema": check_rag_dataset,
    "corpus_coverage": check_corpus_coverage,
    "dataset_difficulty_mix": check_difficulty_mix,
    "reranker_invariant": check_reranker_invariant,
    "promptfoo_configs": check_promptfoo_configs,
    "llm_baseline": check_llm_baseline,
    "candidate_das_fresh": check_candidate_das,
}

_THRESHOLD_AWARE = frozenset({"rag_dataset_schema", "reranker_invariant"})


def run_one(
    name: str, root: Path = REPO_ROOT, thresholds: Thresholds = DEFAULT_THRESHOLDS
) -> list[Finding]:
    if name not in GATES:
        raise KeyError(f"gate desconhecido: {name}")
    gate = GATES[name]
    return list(gate(root, thresholds) if name in _THRESHOLD_AWARE else gate(root))


def run_all(root: Path = REPO_ROOT, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> list[Finding]:
    findings: list[Finding] = []
    for name in GATES:
        findings.extend(run_one(name, root, thresholds))
    return findings


def normalize_promptfoo_results(payload: Any) -> dict[str, bool]:
    """Reduz a saida do promptfoo a {nome_do_caso: passou}. Tolera o envelope de --output.

    Recusa payload vazio ou desconhecido em vez de devolver {}: um gate que
    aceita "nenhum resultado" como "nenhuma regressao" e um falso verde.
    """
    for _ in range(2):
        if isinstance(payload, dict):
            if "results" not in payload:
                raise ValueError("formato de resultado do promptfoo nao reconhecido")
            payload = payload["results"]
    if not isinstance(payload, list) or not payload:
        raise ValueError("formato de resultado do promptfoo nao reconhecido")

    cases: dict[str, bool] = {}
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict) or "pass" not in entry:
            raise ValueError(f"entrada #{index} do promptfoo sem campo 'pass'")
        test_case = entry.get("testCase") or {}
        name = (
            test_case.get("description")
            or test_case.get("vars", {}).get("description")
            or (test_case.get("vars") or {}).get("query")
            or f"case_{index}"
        )
        if name in cases:
            name = f"{name}#{index}"
        cases[str(name)] = bool(entry["pass"])
    return cases


def compare_promptfoo(current: dict[str, bool], baseline: dict[str, bool]) -> list[Finding]:
    check = "promptfoo_regression"
    findings: list[Finding] = []
    regressed = sorted(
        name for name, passed in current.items() if not passed and baseline.get(name, True)
    )
    dropped = sorted(set(baseline) - set(current))
    improved = sorted(
        name for name, passed in current.items() if passed and baseline.get(name) is False
    )
    if regressed:
        findings.append(
            _fail(check, f"casos que passaram no baseline e falharam agora: {regressed}")[0]
        )
    if dropped:
        findings.append(
            _warn(check, f"casos do baseline ausentes no resultado atual: {dropped}")[0]
        )
    if improved:
        findings.append(
            _warn(check, f"novos casos passando (nao estao no baseline): {improved}")[0]
        )
    passed = sum(1 for value in current.values() if value)
    if not regressed and current:
        findings.append(_warn(check, f"sem regressao; {passed}/{len(current)} casos passaram")[0])
    return findings or _ok(check)
