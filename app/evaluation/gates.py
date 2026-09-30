from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

PIPELINE_INTERFACE_TYPES = frozenset(
    {
        "odata",
        "rfc",
        "servicenow",
        "salesforce",
        "workday",
        "ariba",
        "successfactors",
        "cap",
        "apim",
        "po",
    }
)
DIFFICULTIES = frozenset({"easy", "medium", "hard", "out_of_scope"})
REQUIRED_CASE_FIELDS = ("query", "expected_sources", "difficulty")

RAG_DATASET = Path("data/eval/rag_eval_dataset.json")
RERANKER_BENCHMARK = Path("data/eval/reranker_benchmark_results.json")
PROMPTFOO_BASELINE = Path("data/eval/promptfoo_baseline.json")
PROMPT_BASELINE = Path("data/eval/prompt_baseline.json")
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


def check_prompt_digest(root: Path = REPO_ROOT) -> list[Finding]:
    """DA-53: o prompt em producao tem que ser o prompt MEDIDO.

    Moldado no `reranker_invariant` (DA-29), que faz a mesma coisa para o
    cross-encoder: o winner do benchmark tem que ser o que roda. Aqui o
    papel do winner e' o digest do prompt em producao e o do benchmark e' o
    digest gravado em `data/eval/prompt_baseline.json`, no momento em que
    o promptfoo mediu 10/10.

    **Por que isso e' verificavel quando o promptfoo roda o pipeline real.**
    `scripts/promptfoo_provider.py` chama `run_diagnosis`, entao a medicao
    NAO mede uma copia do prompt: mede exatamente o texto de
    `app/agent/prompts.py`. Divergir os dois digests significa que o texto
    mudou depois da medicao -- e o 10/10 passou a descrever outra coisa.

    O digest cobre tambem as `description=` do `DiagnosisModel`, que o
    LangChain injeta no schema de tool-calling: editar um Field sem tocar
    no modulo de prompt tambem reprova este gate, que e' exatamente o
    ponto (o bug ja aconteceu uma vez, ver state.py:18-24).
    """
    check = "prompt_digest_measured"
    try:
        from app.agent.prompts import compute_digest

        production = compute_digest()
    except Exception as exc:  # noqa: BLE001 - o gate nao pode quebrar o build
        return _fail(check, f"nao foi possivel calcular o digest de producao: {exc}")

    try:
        baseline = _load_json(root / PROMPT_BASELINE, check)
    except ValueError as exc:
        # Ausente = ainda nao medido. Nao reprova: assim como o
        # `llm_baseline`, o primeiro registro e' trabalho humano.
        return _warn(check, f"{PROMPT_BASELINE} ausente ({exc}); grave o digest medido")

    if not isinstance(baseline, dict):
        return _fail(check, f"{PROMPT_BASELINE} deveria ser um objeto JSON")

    measured = baseline.get("prompt_digest")
    if not isinstance(measured, str) or len(measured) != 64:
        return _fail(check, f"{PROMPT_BASELINE} sem 'prompt_digest' de 64 chars (sha256)")

    if production != measured:
        return _fail(
            check,
            f"prompt em producao diverge do medido: producao={production[:16]} "
            f"medido={measured[:16]} (v{baseline.get('prompt_version', '?')}). "
            "O prompt foi alterado depois da medicao - reexecute o promptfoo e "
            "regrave com --write-prompt-baseline, ou reverta o texto.",
        )

    # Nao ha check separado de `prompt_version`: `PROMPT_VERSION` faz parte
    # do payload canonico do digest, entao divergir a versao ja divergiu o
    # digest e o check acima ja reprovou. Um segundo check aqui seria um
    # branch que nunca pode disparar.
    return _ok(check)


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
    # Ancorado no INICIO DA LINHA do titulo, nao na primeira ocorrencia da
    # frase: a tabela de DAs do proprio CLAUDE.md cita "das DAs candidatas"
    # numa celula, e uma busca ingenua casava la e devolvia lista vazia.
    block = re.search(
        r"^[ \t]*(?:#{1,4}[ \t]*)?\**DAs candidatas[^\n]*\n(?P<body>.*?)(?:\n[ \t]*\n|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not block:
        return []
    return sorted(set(re.findall(r"DA-\d+", block.group("body"))))


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


def check_preflight_delegates(root: Path = REPO_ROOT) -> list[Finding]:
    """O preflight de RAM do harness tem que viver no modulo, nao no script.

    Sem este gate, a aritmetica pode voltar para dentro do
    scripts/promptfoo_remote.sh e a suite continua verde: os testes
    passariam para `app/evaluation/ram_preflight.py`, que ninguem mais
    chamaria. E' a mesma razao de `reranker_invariant`: o teste precisa
    vigiar o caminho de codigo que realmente executa.
    """
    check = "preflight_delegates"
    script = root / "scripts/promptfoo_remote.sh"
    if not script.is_file():
        return _fail(check, f"{script} nao encontrado")

    source = script.read_text(encoding="utf-8")
    if "PYEOF" in source or "import json" in source:
        return _fail(
            check,
            "o preflight voltou a ser python inline no script; a aritmetica "
            "precisa ficar em app/evaluation/ram_preflight.py para ter cobertura",
        )
    if "app.evaluation.ram_preflight" not in source:
        return _fail(check, "o script nao invoca app/evaluation/ram_preflight")
    if not (root / "app/evaluation/ram_preflight.py").is_file():
        return _fail(check, "app/evaluation/ram_preflight.py nao encontrado")
    return _ok(check)


def _das(lista: list[int] | set[int] | tuple) -> str:
    return "[" + ", ".join(f"DA-{d}" for d in sorted(lista)) + "]"


def _documented_das(root: Path) -> tuple[dict[int, int], set[int]]:
    """DAs com secao de prosa no README, e DAs so no ARCHITECTURE.md.

    O README e' a fonte canonica da prosa de decisao (CLAUDE.md, passo 3 do
    registro de DA). O ARCHITECTURE.md e' aceite como local ALTERNATIVO
    declarado: ele tem 4 DAs (32-35) cujo design vive la e mover o texto
    so' custaria churn. O que o gate proibe e' o silencio.

    Reconhece os dois formatos de rotulo: `(DA-30)` e o agrupado
    `(DA-46/47/48)` / `(DA-15/16/17)`, que o proprio projeto ja usa para
    DAs entregues na mesma mudanca.
    """
    readme = (root / "README.md").read_text(encoding="utf-8")
    area = readme[readme.index("## Decisões de Arquitetura") :]
    in_readme: dict[int, int] = {}
    for num, titulo in re.findall(r"^### (\d+)\.\s*(.+?)\s*$", area, re.MULTILINE):
        for grupo in re.findall(r"\(DA-([\d/]+)\)\s*$", titulo):
            for n in grupo.split("/"):
                in_readme[int(n)] = int(num)

    architecture = (root / ARCHITECTURE_DOC).read_text(encoding="utf-8")
    in_arch = {
        int(d) for d in re.findall(r"^#{1,6}\s.+?\(DA-(\d+)\)\s*$", architecture, re.MULTILINE)
    }
    return in_readme, in_arch


def _registered_das(root: Path) -> set[int]:
    """DAs na tabela de registro do CLAUDE.md, expandindo `DA-4/8`."""
    text = (root / CLAUDE_DOC).read_text(encoding="utf-8")
    registradas: set[int] = set()
    for linha in re.findall(r"^\|\s*(DA-[\d/]+)\s*\|", text, re.MULTILINE):
        for n in linha.replace("DA-", "").split("/"):
            registradas.add(int(n))
    return registradas


def check_documented_das(root: Path = REPO_ROOT) -> list[Finding]:
    """Toda DA registrada tem prosa localizavel — nas DUAS direcoes.

    O `candidate_das_fresh` (DA-51) checa uma direcao so: que uma DA
    marcada como candidata NAO esteja entregue. O inverso nunca foi
    verificado, e o resultado foi 15 secoes de decisao no README sem
    rotulo `(DA-N)` — invisiveis para qualquer `grep "DA-15"` — e 1 DA
    (DA-30) sem seção propria em lugar nenhum. A prosa existia; a
    amarração nao. Este gate e' a amarração.

    Reprova tambem o caminho inverso: secao `(DA-N)` no README que nao
    esta no registro do CLAUDE.md e' prosa orfa, que o proximo
    registrador nao vai encontrar.
    """
    check = "implemented_das_documented"
    registradas = _registered_das(root)
    if not registradas:
        return _warn(check, "nenhuma DA encontrada na tabela de registro do CLAUDE.md")

    in_readme, in_arch = _documented_das(root)
    sem_prosa = sorted(registradas - set(in_readme) - in_arch)
    if sem_prosa:
        return _fail(
            check,
            f"DA registrada sem prosa: {_das(sem_prosa)} "
            "(sem secao no README nem em docs/ARCHITECTURE.md)",
        )

    orfas = sorted(set(in_readme) - registradas)
    if orfas:
        return _fail(
            check,
            f"secao (DA-N) no README fora do registro do CLAUDE.md: {_das(orfas)}",
        )
    return _ok(check)


def check_index_current(root: Path = REPO_ROOT) -> list[Finding]:
    """O indice de DAs do README tem que refletir os headings de verdade.

    O indice e' gerado a mao (nao ha build step), entao ele proprio
    apodrece: sem esta checagem, o indice continua "verde" apontando para
    numeros de secao que mudaram, que e' pior do que nao ter indice —
    porque parece navegavel e nao e.

    Duas classes de apodrecimento, ambas achadas em revisao real do
    README: o indice chegou a estar COLADO tres vezes seguidas (duas
    copias stale com numeros de secao defasados por um), e o numero da
    coluna "Seção" nunca foi conferido contra os headings — a versao
    anterior usava `re.search` (via so a primeira tabela) e comparava
    CONJUNTOS de DAs, entao duplicata era invisivel e o mapeamento
    errado passava.
    """
    check = "das_index_current"
    readme = (root / "README.md").read_text(encoding="utf-8")
    tabelas = re.findall(
        r"^\| DA \| Seção \| O que é \|\n\|---\|---\|---\|\n(.*?)(?:\n\n|\Z)",
        readme,
        re.MULTILINE | re.DOTALL,
    )
    if not tabelas:
        return _fail(check, "tabela de indice de DAs nao encontrada no README.md")
    if len(tabelas) > 1:
        return _fail(
            check,
            f"indice de DAs duplicado: {len(tabelas)} tabelas no README.md"
            " — mantenha apenas a mais recente",
        )
    corpo = tabelas[0]

    declarados = {int(n) for n in re.findall(r"^\|\s*(\d+)\s*\|", corpo, re.MULTILINE)}
    registradas = _registered_das(root)
    if declarados != registradas:
        faltando = sorted(registradas - declarados)
        sobrando = sorted(declarados - registradas)
        detalhe = []
        if faltando:
            detalhe.append(f"faltando {_das(faltando)}")
        if sobrando:
            detalhe.append(f"sem registro no CLAUDE.md {_das(sobrando)}")
        return _fail(check, f"indice de DAs dessincronizado: {'; '.join(detalhe)}")

    # Toda linha do indice tem que apontar para uma secao que existe.
    in_readme, in_arch = _documented_das(root)
    quebradas = [
        n
        for n in declarados
        if f"| {n} | **—** |" not in corpo and n not in in_readme and n not in in_arch
    ]
    if quebradas:
        return _fail(check, f"indice aponta para secao inexistente: {_das(quebradas)}")

    # O numero da coluna "Seção" tem que ser o numero do heading REAL —
    # nao apenas um numero que exista em algum lugar. Indice stale aponta
    # para a secao vizinha e continua parecendo navegavel.
    desencontradas: list[str] = []
    for linha in corpo.splitlines():
        m = re.match(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|", linha)
        if not m:
            continue
        da, celula = int(m.group(1)), m.group(2)
        numero = re.match(r"\[(\d+)\]", celula)
        if numero:
            real = in_readme.get(da)
            if real is None:
                desencontradas.append(
                    f"DA-{da} aponta para secao {numero.group(1)} mas nao tem prosa no README.md"
                )
            elif real != int(numero.group(1)):
                desencontradas.append(
                    f"DA-{da}: indice diz secao {numero.group(1)}, a real e {real}"
                )
        elif celula == "ARCHITECTURE" and da not in in_arch:
            desencontradas.append(
                f"DA-{da} rotulada ARCHITECTURE mas sem prosa em {ARCHITECTURE_DOC}"
            )
    if desencontradas:
        return _fail(check, "; ".join(desencontradas))
    return _ok(check)


# ---------------------------------------------------------------------------
# Integridade da documentacao (DA-51)
#
# Os tres gates abaixo cobrem a classe de bug que a suite de testes nao ve:
# a documentacao afirmar coisas que o codigo contradiz. Nenhum deles chama
# LLM nem infra — sao verificacoes de texto, entao rodam em qualquer CI.
# ---------------------------------------------------------------------------

DOCS_DIR = Path("docs")
PIPELINE_MODEL_SOURCE = Path("app/models.py")
CONNECTORS_SOURCE = Path("app/connectors/__init__.py")
SUPERVISOR_SOURCE = Path("app/agent/supervisor.py")
ADMIN_SYSTEMS_SOURCE = Path("app/admin/models.py")
CLI_SOURCE = Path("app/agent/graph.py")
UI_DROPDOWN_SOURCE = Path("frontend/src/components/DiagnoseView.tsx")


def _iter_docs(root: Path) -> list[Path]:
    docs = root / DOCS_DIR
    if not docs.is_dir():
        return []
    return sorted(docs.rglob("*.md"))


def _markdown_docs(root: Path) -> list[Path]:
    """docs/ mais os dois markdown da raiz que fazem indice do conjunto."""
    docs = _iter_docs(root)
    for extra in (Path("README.md"), Path("CLAUDE.md")):
        if (root / extra).is_file():
            docs.append(root / extra)
    return docs


def check_docs_markup_integrity(root: Path = REPO_ROOT) -> list[Finding]:
    """Fences de codigo balanceadas e links relativos para .md resolvendo.

    Um arquivo truncado no meio de um heredoc deixa o par de fences impar e
    engole todo o resto da renderizacao. Nao ha teste que pegue isso: o
    arquivo .md nao e importado por ninguem.
    """
    check = "docs_markup_integrity"
    problemas: list[str] = []

    for doc in _markdown_docs(root):
        texto = doc.read_text(encoding="utf-8")
        fences = len(re.findall(r"^```", texto, flags=re.MULTILINE))
        if fences % 2:
            problemas.append(
                f"{doc.relative_to(root)}: {fences} fences (impar) — bloco de codigo nao fecha"
            )

        for alvo in re.findall(r"\]\(([^)#\s]+\.md)\)", texto):
            if alvo.startswith(("http://", "https://")):
                continue
            if not (doc.parent / alvo).resolve().exists():
                problemas.append(f"{doc.relative_to(root)}: link quebrado -> {alvo}")

    if problemas:
        return _fail(check, "; ".join(sorted(problemas)[:8]))
    return _ok(check)


_CODE_REF = re.compile(
    r"`((?:app|tests|scripts)/[A-Za-z0-9_/]+\.py)(?:::([A-Za-z_][A-Za-z0-9_]*)|:(\d+))`"
)

_MD_CITACAO = re.compile(r"`([\w./-]+\.md)`")


def check_docs_code_references(root: Path = REPO_ROOT) -> list[Finding]:
    """Referencias `app/x.py::simbolo`, `app/x.py:N` e `arquivo.md` tem de resolver.

    Cobre a forma precisa de citar codigo, que e a que a documentacao de
    debug usa para mandar o leitor abrir um breakpoint. Um tutorial que
    aponta `app/agent/graph.py` para uma funcao que mora em nodes.py, ou
    para um simbolo que foi deletado, manda o leitor procurar algo que nao
    existe — e nenhum teste de Python falha por causa disso.

    Citacao de arquivo `.md` em backticks tambem tem de resolver em algum
    lugar real (raiz do repo, `docs/`, `data/sample_docs/` ou ao lado do
    doc que cita). O caso real: DEZ citacoes, em tres docs e em docstrings
    de codigo, apontavam para um `learnings.md` que NUNCA existiu no
    historico do git — backlog prometido, arquivo fantasma.

    Deliberadamente NAO checa identificadores em prosa solta (`ANTHROPIC_API_KEY`,
    `RFC_SYSTEM_INFO`, `QDRANT_HOST_PORT`): varios desses sao nomes de
    funcao ABAP, variavel de shell ou provider nao suportado, e um gate
    que accuse falso positivo vira gate que ninguem ouve. Citacoes COMPOSTAS
    (`` `a.md, b.md` `` num backtick so) tambem ficam de fora — o regex de
    caminho nao casa com virgula, e e' exatamente isso que impede o falso
    positivo.
    """
    check = "docs_code_references"
    problemas: list[str] = []

    for doc in _markdown_docs(root):
        texto = doc.read_text(encoding="utf-8")
        for caminho, simbolo, linha in _CODE_REF.findall(texto):
            alvo = root / caminho
            if not alvo.is_file():
                problemas.append(f"{doc.relative_to(root)}: {caminho} nao existe")
                continue
            if simbolo:
                fonte = alvo.read_text(encoding="utf-8")
                padroes = (
                    rf"^\s*(?:async\s+)?def\s+{re.escape(simbolo)}\b",
                    rf"^\s*{re.escape(simbolo)}\s*(?::[^=\n]+)?=",
                    rf"^\s*(?:class|{re.escape(simbolo)})\b",
                )
                if not any(re.search(p, fonte, flags=re.MULTILINE) for p in padroes):
                    problemas.append(
                        f"{doc.relative_to(root)}: {simbolo} nao definido em {caminho}"
                    )
            elif linha:
                total = len(alvo.read_text(encoding="utf-8").splitlines())
                if int(linha) > total:
                    problemas.append(
                        f"{doc.relative_to(root)}: {caminho} tem {total} linhas, doc cita a {linha}"
                    )

        for citacao in set(_MD_CITACAO.findall(texto)):
            bases = (root, root / "docs", root / "data" / "sample_docs", doc.parent)
            if not any((base / citacao).exists() for base in bases):
                problemas.append(
                    f"{doc.relative_to(root)}: cita {citacao} que nao existe em lugar nenhum"
                )

    if problemas:
        return _fail(check, "; ".join(sorted(problemas)[:8]))
    return _ok(check)


def _dict_keys(bloco: str, nome: str) -> set[str]:
    """Chaves de um literal de dict nomeado no fonte. Ancorar no nome do
    dict evita varrer qualquer string solta do arquivo — sem isso o gate
    acusou 'note' e 'status', que sao chaves do return de
    connector_status(), nao conectores."""
    match = re.search(
        rf"^{re.escape(nome)}[^=\n]*=\s*\{{(.*?)^\s*\}}", bloco, flags=re.MULTILINE | re.DOTALL
    )
    if match is None:
        raise ValueError(f"{nome} nao encontrado em {CONNECTORS_SOURCE}")
    return set(re.findall(r"""['"]([a-z_]+)['"]\s*:""", match.group(1)))


def _registered_connectors(root: Path) -> set[str]:
    fonte = (root / CONNECTORS_SOURCE).read_text(encoding="utf-8")
    conectores = _dict_keys(fonte, "_REGISTRY")
    settings = _dict_keys(fonte, "_REAL_MODE_SETTING")
    if conectores != settings:
        raise ValueError(
            f"_REGISTRY e _REAL_MODE_SETTING divergem: "
            f"so no registry {sorted(conectores - settings)}, so nos settings {sorted(settings - conectores)}"
        )
    return conectores


def check_connector_reachable(root: Path = REPO_ROOT) -> list[Finding]:
    """Todo conector registrado precisa ser aceito pelo Literal do pipeline.

    `app/connectors/__init__.py` registra SuccessFactors, mas o Literal
    fechado de `IncidentRequest.interface_type` aceitava so 8 valores: o
    conector existia, tinha credencial no `.env.example`, e mesmo assim
    `/diagnose` respondia 422 se alguém pedisse. O gate `rag_dataset_schema`
    espelhava o Literal, entao os dois concordavam — e o bug passava. Aqui a
    fonte da verdade e o registro de conectores, nao o Literal.
    """
    check = "connector_reachable"
    try:
        conectores = _registered_connectors(root)
    except FileNotFoundError:
        return _fail(check, f"{CONNECTORS_SOURCE} nao existe")
    except ValueError as exc:
        return _fail(check, str(exc))
    if not conectores:
        return _fail(check, f"nao consegui ler os conectores registrados de {CONNECTORS_SOURCE}")

    problemas: list[str] = []

    # O Literal do pipeline e lido do ARQUIVO, nao da constante deste
    # modulo: se os dois viessem da mesma fonte, o gate passaria junto com o
    # defeito em vez de accusationa-lo. A constante so precisa espelhar o
    # arquivo, e isso tambem e verificado.
    fonte_models = (root / PIPELINE_MODEL_SOURCE).read_text(encoding="utf-8")
    literais: set[str] = set()
    # Cada Literal e' conferido SEPARADAMENTE antes da uniao. `app/models.py`
    # tem dois `interface_type` (IncidentRequest e o envelope de resposta) e a
    # uniao mascararia a queda de um deles: o conector continuaria no conjunto
    # pelo outro, o gate passaria, e o envelope voltaria a rejeitar 422 o que a
    # request aceita. A union so serve para o espelho da constante abaixo.
    corpos = re.findall(
        r"^\s*interface_type:?\s*(?:\(\s*)?Literal\[([^\]]*)\]", fonte_models, re.MULTILINE
    )
    for corpo in corpos:
        literais |= set(re.findall(r"""['"]([a-z_]+)['"]""", corpo))
    if not literais:
        problemas.append(f"{PIPELINE_MODEL_SOURCE}: nenhum Literal de interface_type encontrado")
    else:
        for indice, corpo in enumerate(corpos, start=1):
            faltando_aqui = sorted(conectores - set(re.findall(r"""['"]([a-z_]+)['"]""", corpo)))
            if faltando_aqui:
                problemas.append(
                    f"{PIPELINE_MODEL_SOURCE}: Literal #{indice} de interface_type nao aceita "
                    f"{faltando_aqui} (aceito pelo outro Literal, rejeitado por este)"
                )

    faltando = sorted(conectores - literais)
    if faltando:
        problemas.append(
            f"conectores registrados que o Literal do pipeline rejeita: {faltando}"
            " (/diagnose responde 422 para eles)"
        )

    if literais and literais != PIPELINE_INTERFACE_TYPES:
        problemas.append(
            f"PIPELINE_INTERFACE_TYPES {sorted(PIPELINE_INTERFACE_TYPES)} nao espelha o Literal de "
            f"{PIPELINE_MODEL_SOURCE} {sorted(literais)}"
        )

    for doc in (PIPELINE_MODEL_SOURCE, ADMIN_SYSTEMS_SOURCE):
        fonte = (root / doc).read_text(encoding="utf-8")
        for conector in sorted(conectores):
            # match por palavra, nao pela forma `"x"`: o docstring do catalogo
            # admin lista os valores separados por "/" e em prosa.
            if not re.search(rf"\b{re.escape(conector)}\b", fonte):
                problemas.append(f"{doc} nao menciona o conector {conector}")

    # O supervisor deriva os conjuntos dele do Literal: um Literal com 9
    # valores e um supervisor que so conhece 8 roteia um deles para generic.
    supervisor = (root / SUPERVISOR_SOURCE).read_text(encoding="utf-8")
    cobertos: set[str] = set()
    for nome in ("_SAP_INTERFACE_TYPES", "_SAAS_INTERFACE_TYPES"):
        bloco = re.search(rf"{re.escape(nome)}\s*=\s*\{{([^}}]*)\}}", supervisor)
        if bloco is None:
            problemas.append(f"{SUPERVISOR_SOURCE}: {nome} nao encontrado")
            continue
        cobertos |= set(re.findall(r"""['"]([a-z_]+)['"]""", bloco.group(1)))
    # apim e cross-vendor de proposito: cai em generic por design.
    orphans = sorted(conectores - cobertos - {"apim"})
    if orphans:
        problemas.append(
            f"{SUPERVISOR_SOURCE}: nem _SAP_INTERFACE_TYPES nem _SAAS_INTERFACE_TYPES cobrem {orphans} "
            "— caem em generic mesmo com interface_type explicito"
        )

    # A quarta superficie e o CLI: as choices do --interface em
    # app/agent/graph.py. Caso real: successfactors foi aceito pelo
    # Literal, coberto pelo supervisor e listado no catalogo admin, e o
    # argparse do CLI ainda o rejeitava — a mesma morte do conector em
    # outra superficie, invisivel para as tres checagens de cima.
    try:
        cli_fonte = (root / CLI_SOURCE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return _fail(check, f"{CLI_SOURCE} nao existe — superficie CLI sem checagem")
    m_choices = re.search(r"--interface[^[]*choices=\[([^\]]*)\]", cli_fonte)
    if m_choices is None:
        problemas.append(f"{CLI_SOURCE}: choices do --interface nao encontrado")
    else:
        cli_choices = set(re.findall(r"""['"]([a-z_]+)['"]""", m_choices.group(1)))
        rejeitados = sorted(conectores - cli_choices)
        if rejeitados:
            problemas.append(
                f"{CLI_SOURCE}: --interface rejeita {rejeitados} — "
                "choices nao cobrem o registro de conectores"
            )

    # A quinta superficie e o dropdown da UI web: o SYSTEMS de
    # frontend/src/components/DiagnoseView.tsx. Caso real (homologacao):
    # successfactors aceito pelo Literal, pelo CLI, pelo supervisor e
    # pelo catalogo — e ausente do dropdown, invisivel para quem so
    # opera pela interface. Gates verdes, conector morto na tela.
    try:
        ui_fonte = (root / UI_DROPDOWN_SOURCE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return _fail(check, f"{UI_DROPDOWN_SOURCE} nao existe — dropdown da UI sem checagem")
    m_ui = re.search(r"const SYSTEMS[^=]*=\s*\[(.*?)\];", ui_fonte, re.DOTALL)
    if m_ui is None:
        problemas.append(f"{UI_DROPDOWN_SOURCE}: dropdown SYSTEMS nao encontrado")
    else:
        ui_valores = {v for v in re.findall(r"\[\s*'([a-z_]*)'\s*,", m_ui.group(1)) if v}
        ausentes = sorted(conectores - ui_valores)
        if ausentes:
            problemas.append(f"{UI_DROPDOWN_SOURCE}: dropdown nao oferece {ausentes} ao usuario")

    # A sexta superficie e' o catalogo de sistemas do admin (DA-49), que
    # mantem a propria tupla `CONNECTOR_TYPES` para poder casar incidente
    # com sistema na correlacao (DA-50). Caso real (achado no DA-56):
    # `successfactors` era aceito pelo Literal, coberto pelo supervisor,
    # oferecido pelo CLI e pelo dropdown — e nao estava em `CONNECTOR_TYPES`,
    # onde a propria prosa deste arquivo dizia que estava. A correlacao
    # incident->system nao tinha por onde casar um SuccessFactors, e nenhuma
    # das cinco checagens acima via isso: o gate afirmava cobrir o catalogo
    # sem nunca le-lo.
    try:
        admin_fonte = (root / ADMIN_SYSTEMS_SOURCE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return _fail(check, f"{ADMIN_SYSTEMS_SOURCE} nao existe — catalogo admin sem checagem")
    m_cat = re.search(r"CONNECTOR_TYPES\s*=\s*\(([^)]*)\)", admin_fonte, re.DOTALL)
    if m_cat is None:
        problemas.append(f"{ADMIN_SYSTEMS_SOURCE}: CONNECTOR_TYPES nao encontrado")
    else:
        cat = set(re.findall(r"""['"]([a-z_]+)['"]""", m_cat.group(1)))
        fora = sorted(conectores - cat)
        if fora:
            problemas.append(
                f"{ADMIN_SYSTEMS_SOURCE}: CONNECTOR_TYPES nao cobre {fora} — a correlacao "
                "incidente->sistema (DA-50) nao tem por onde casar esses conectores"
            )

    if problemas:
        return _fail(check, "; ".join(problemas[:6]))
    return _ok(check)


GATES = {
    "rag_dataset_schema": check_rag_dataset,
    "corpus_coverage": check_corpus_coverage,
    "dataset_difficulty_mix": check_difficulty_mix,
    "reranker_invariant": check_reranker_invariant,
    "promptfoo_configs": check_promptfoo_configs,
    "llm_baseline": check_llm_baseline,
    "candidate_das_fresh": check_candidate_das,
    "implemented_das_documented": check_documented_das,
    "das_index_current": check_index_current,
    "preflight_delegates": check_preflight_delegates,
    "prompt_digest_measured": check_prompt_digest,
    "docs_markup_integrity": check_docs_markup_integrity,
    "docs_code_references": check_docs_code_references,
    "connector_reachable": check_connector_reachable,
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
