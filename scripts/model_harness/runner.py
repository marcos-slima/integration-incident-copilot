"""Execucao de uma (modelo x tarefa x repeticao) num sandbox isolado.

Desenho de SEGURANCA (o harness roda `opencode run --auto`, ou seja, sem
ninguem aprovando nada) - por isso o sandbox:

- Cada execucao acontece em `/tmp/opencode/model_harness/<modelo>/<tarefa>/`,
  uma copia DESCARTavel da fixture. O repositorio real nunca e tocado.
- Um `opencode.json` proprio do sandbox NEGA `shell`, `webfetch`, `task`
  (subagente) e `external_directory`. O modelo so pode ler/escrever dentro
  do sandbox; nao consegue disparar comando de shell nem sair para a rede.
  `--allow-bash` reabre isso conscientemente para quem quiser medir o loop
  agentico completo (e assumir o risco).
- O veredito e do `pytest` rodando um arquivo oculto, copiado para o
  sandbox DEPOIS da edicao. O modelo nunca ve o teste.

Metricas por execucao: acerto (pytest), precisao (arquivos congelados +
`git diff --stat`) e velocidade (wall, tokens/s, passos), lidas do stream
NDJSON que o proprio `opencode run --format json` emite.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from scripts.model_harness.tasks import Task

SANDBOX_ROOT = Path(os.environ.get("MODEL_HARNESS_DIR", "/tmp/opencode/model_harness"))

# Permissao negada explicitamente no sandbox: sem shell, sem rede, sem
# subagente, sem sair do diretorio. A ultima regra que casa vence (mesma
# semantica do config V2), e estas vem DEPOIS do "allow *" herdado - por
# isso negam. O nome da acao de shell no config V2 e' `shell` (nao `bash`,
# que e' o nome da ferramenta); negamos os dois por seguranca.
_DENY_SANDBOX = ("shell", "bash", "webfetch", "task", "external_directory")


@dataclass
class RunResult:
    """Resultado de uma execucao. Nunca contem juizo do modelo - so fatos."""

    model: str
    task: str
    repeat: int
    passed: bool
    pytest_ok: bool
    frozen_ok: bool
    timed_out: bool
    error: str | None
    wall_s: float
    steps: int
    tokens_in: int
    tokens_out: int
    tools: dict[str, int] = field(default_factory=dict)
    files_edited: list[str] = field(default_factory=list)
    diff_stat: str = ""
    pytest_tail: str = ""
    # Guarda anti-escape: True se algum tool_use tocou caminho FORA do
    # sandbox. Deve ser impossivel com --standalone; se virar True, o
    # veredito e' invalido (o modelo mexeu no repo real).
    escaped: bool = False

    @property
    def tok_s(self) -> float:
        """Tokens de saida por segundo (proxy de responsividade percebida)."""
        return round(self.tokens_out / self.wall_s, 1) if self.wall_s > 0 else 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tok_s"] = self.tok_s
        return d


def _opencode_bin() -> str:
    """Localiza o binario do OpenCode (PATH ou instalacao padrao)."""
    found = shutil.which("opencode")
    if found:
        return found
    fallback = Path.home() / ".opencode" / "bin" / "opencode"
    if fallback.is_file():
        return str(fallback)
    raise FileNotFoundError("binario 'opencode' nao encontrado no PATH nem em ~/.opencode/bin")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _materializar(task: Task, destino: Path) -> None:
    """Cria o sandbox: limpa, escreve a fixture e faz um commit inicial."""
    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir(parents=True)
    for nome, conteudo in task.work.items():
        alvo = destino / nome
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_text(conteudo, encoding="utf-8")

    permissoes = [{"action": "*", "resource": "*", "effect": "allow"}]
    permissoes += [{"action": a, "resource": "*", "effect": "deny"} for a in _DENY_SANDBOX]
    (destino / "opencode.json").write_text(
        json.dumps(
            {"$schema": "https://opencode.ai/config.json", "permissions": permissoes}, indent=2
        ),
        encoding="utf-8",
    )
    _git(destino, "init", "-q")
    _git(destino, "config", "user.email", "harness@local")
    _git(destino, "config", "user.name", "model-harness")
    _git(destino, "add", "-A")
    _git(destino, "commit", "-qm", "fixture")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _parse_stream(saida: str) -> dict:
    """Extrai fatos do NDJSON do `opencode run --format json`."""
    tools: dict[str, int] = {}
    files: list[str] = []
    steps = 0
    tok_in = 0
    tok_out = 0
    for linha in saida.splitlines():
        linha = linha.strip()
        if not linha.startswith("{"):
            continue
        try:
            ev = json.loads(linha)
        except json.JSONDecodeError:
            continue
        tipo = ev.get("type")
        parte = ev.get("part") or {}
        if tipo == "step_start":
            steps += 1
        elif tipo == "tool_use":
            ferramenta = parte.get("tool", "?")
            tools[ferramenta] = tools.get(ferramenta, 0) + 1
            estado = parte.get("state") or {}
            entrada = estado.get("input") or {}
            caminho = entrada.get("path") or entrada.get("filePath")
            if ferramenta in {"edit", "write", "patch"} and caminho and caminho not in files:
                files.append(caminho)
        elif tipo == "step_finish":
            tk = parte.get("tokens") or {}
            tok_in += int(tk.get("input") or 0)
            tok_out += int(tk.get("output") or 0)
    return {"tools": tools, "files": files, "steps": steps, "tok_in": tok_in, "tok_out": tok_out}


def _rodar_modelo(task: Task, modelo: str, sandbox: Path, timeout: int, allow_bash: bool) -> dict:
    """Chama `opencode run` no sandbox e devolve os fatos do stream + wall."""
    binario = _opencode_bin()
    cmd = [
        binario,
        "run",
        # --standalone: servidor PRIVADO com o diretorio do sandbox como
        # projeto. SEM isto o `run` fala com o servidor em background, cujo
        # projeto e' o repositorio real -> o modelo escreve no repo, nao no
        # sandbox (bug observado no primeiro smoke, 2026-10-11).
        "--standalone",
        "-m",
        modelo,
        "--auto",
        "--format",
        "json",
        "--title",
        f"harness-{task.id}",
        task.instruction,
    ]
    if allow_bash:
        # reescreve o config do sandbox reabrindo bash
        permissoes = [{"action": "*", "resource": "*", "effect": "allow"}]
        (sandbox / "opencode.json").write_text(
            json.dumps(
                {"$schema": "https://opencode.ai/config.json", "permissions": permissoes}, indent=2
            ),
            encoding="utf-8",
        )
    env = dict(os.environ)
    # O OpenCode resolve o diretorio do PROJETO por PWD, nao pelo cwd real
    # do processo: `subprocess(cwd=...)` nao atualiza PWD, entao sem isto o
    # modelo enxerga o repositorio real e escreve nele (bug observado no
    # primeiro smoke, 2026-10-11). --standalone + PWD juntos isolam de fato.
    env["PWD"] = str(sandbox)
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=sandbox, env=env, capture_output=True, text=True, timeout=timeout, check=False
        )
        saida, timed_out, erro = proc.stdout, False, None
    except subprocess.TimeoutExpired as exc:
        saida = exc.stdout or ""
        if isinstance(saida, bytes):
            saida = saida.decode("utf-8", "replace")
        timed_out, erro = True, f"timeout apos {timeout}s"
    wall = time.time() - t0
    fatos = _parse_stream(saida)
    fatos.update({"wall": wall, "timed_out": timed_out, "error": erro})
    return fatos


def _rodar_pytest(sandbox: Path, task: Task) -> tuple[bool, str]:
    """Copia o teste oculto e roda pytest SÓ nele. Devolve (ok, tail)."""
    nome = f"test_harness_{task.id}.py"
    (sandbox / nome).write_text(task.hidden, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", nome, "-q", "-p", "no:cacheprovider", "--no-header"],
        cwd=sandbox,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    ok = proc.returncode == 0
    saida = (proc.stdout or "") + (proc.stderr or "")
    return ok, "\n".join(saida.strip().splitlines()[-12:])


def _frozen_ok(sandbox: Path, task: Task) -> bool:
    """True se todos os arquivos congelados ficaram byte a byte iguais."""
    for nome in task.frozen:
        original = task.work.get(nome)
        atual = sandbox / nome
        if original is None:
            continue
        if not atual.is_file() or _sha(atual.read_text(encoding="utf-8")) != _sha(original):
            return False
    return True


def _fora_do_sandbox(arquivos: list[str], sandbox: Path) -> list[str]:
    """Caminhos de edicao que ficaram FORA do sandbox.

    Cuidado que ja custou um falso positivo (2026-10-11): o modelo costuma
    reportar caminho RELATIVO (`processar.py`). Resolver um relativo contra
    o cwd do harness (o repositorio) acusa escape onde nao houve. Relativo
    aqui e' sempre sandbox-relativo. E usamos `is_relative_to`, nao
    `startswith`, para o sandbox `/a/b` nao "conter" `/a/bc`.
    """
    raiz = sandbox.resolve()
    fora: list[str] = []
    for bruto in arquivos:
        caminho = Path(bruto)
        if not caminho.is_absolute():
            caminho = sandbox / caminho
        if not caminho.resolve().is_relative_to(raiz):
            fora.append(bruto)
    return fora


def run_one(
    modelo: str, task: Task, repeat: int, timeout: int | None = None, allow_bash: bool = False
) -> RunResult:
    """Executa uma medicao. Nunca levanta por falha do modelo - so reporta."""
    timeout = timeout or task.timeout_s
    sandbox = SANDBOX_ROOT / modelo.replace("/", "__") / f"{task.id}__r{repeat}"
    _materializar(task, sandbox)
    try:
        fatos = _rodar_modelo(task, modelo, sandbox, timeout, allow_bash)
    except Exception as exc:  # noqa: BLE001 - falha de ambiente vira resultado, nao crash
        return RunResult(
            model=modelo,
            task=task.id,
            repeat=repeat,
            passed=False,
            pytest_ok=False,
            frozen_ok=False,
            timed_out=False,
            error=str(exc),
            wall_s=0.0,
            steps=0,
            tokens_in=0,
            tokens_out=0,
        )

    frozen = _frozen_ok(sandbox, task)
    fora = _fora_do_sandbox(fatos["files"], sandbox)
    escaped = bool(fora)
    erro = fatos["error"]
    if escaped:
        erro = f"ESCAPOU do sandbox: {fora}"

    if fatos["timed_out"]:
        pytest_ok, tail = False, "pulado: execucao estourou o tempo"
    else:
        pytest_ok, tail = _rodar_pytest(sandbox, task)

    diff = _git(sandbox, "diff", "--stat").stdout.strip()
    return RunResult(
        model=modelo,
        task=task.id,
        repeat=repeat,
        passed=bool(pytest_ok and frozen and not fatos["timed_out"] and not escaped),
        pytest_ok=pytest_ok,
        frozen_ok=frozen,
        timed_out=fatos["timed_out"],
        error=erro,
        wall_s=round(fatos["wall"], 1),
        steps=fatos["steps"],
        tokens_in=fatos["tok_in"],
        tokens_out=fatos["tok_out"],
        tools=fatos["tools"],
        files_edited=fatos["files"],
        diff_stat=diff,
        pytest_tail=tail,
        escaped=escaped,
    )
