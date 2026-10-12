"""Integridade do harness de refactoring - impede que ele MEÇA A COISA ERRADA.

Mesma disciplina de `tests/test_eval_suite_integrity.py`: o harness so tem
poder se cada tarefa tiver SINAL. Uma tarefa sem sinal e' pior que tarefa
nenhuma, porque produz um placar com cara de medicao. As invariantes
travadas aqui:

1. FALHA no estado inicial - senao "nao fazer nada" pontua.
2. PASSA na solucao de referencia - senao o teste oculto esta errado.
3. O teste oculto importa algo que EXISTE na fixture - senao o veredito
   e' sempre ModuleNotFoundError.
4. O sandbox NEGA bash/webfetch/task - senao o harness roda `--auto` sem
   rede de seguranca.
5. Nenhum valor com prefixo de segredo (`sk-`) nem nome de campo sensivel
   entra nos fontes - o pre-commit (gitleaks) barraria o commit.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.model_harness.provenance import coletar, sem_prefixo
from scripts.model_harness.report import resumir
from scripts.model_harness.runner import RunResult, _fora_do_sandbox, _parse_stream
from scripts.model_harness.tasks import TAREFAS, all_tasks, get_tasks

PACOTE = Path(__file__).resolve().parent.parent / "scripts" / "model_harness"


def _roda_pytest(pasta: Path, task_id: str, hidden: str) -> int:
    nome = f"test_harness_{task_id}.py"
    (pasta / nome).write_text(hidden, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", nome, "-q", "-p", "no:cacheprovider", "--no-header"],
        cwd=pasta,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return proc.returncode


def _escreve(pasta: Path, arquivos: dict[str, str]) -> None:
    for nome, conteudo in arquivos.items():
        alvo = pasta / nome
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_text(conteudo, encoding="utf-8")


@pytest.mark.parametrize("task", all_tasks(), ids=lambda t: t.id)
def test_tarefa_tem_sinal_falha_no_inicio(tmp_path, task):
    """A fixture crua NAO pode passar o teste oculto (senao a tarefa e' gratis)."""
    _escreve(tmp_path, task.work)
    assert _roda_pytest(tmp_path, task.id, task.hidden) != 0, (
        f"tarefa '{task.id}': o teste oculto JA passa na fixture crua -> sem sinal"
    )


@pytest.mark.parametrize("task", all_tasks(), ids=lambda t: t.id)
def test_tarefa_e_verificavel_com_a_solucao(tmp_path, task):
    """A solucao de referencia TEM que passar (senao o teste oculto esta errado)."""
    arquivos = dict(task.work)
    arquivos.update(task.solution)
    _escreve(tmp_path, arquivos)
    assert _roda_pytest(tmp_path, task.id, task.hidden) == 0, (
        f"tarefa '{task.id}': nem a solucao de referencia passa -> teste oculto invalido"
    )


@pytest.mark.parametrize("task", all_tasks(), ids=lambda t: t.id)
def test_estrutura_da_tarefa(task):
    assert re.fullmatch(r"[a-z0-9_]+", task.id), f"id inseguro: {task.id}"
    assert task.instruction.strip(), f"{task.id}: instrucao vazia"
    assert task.work, f"{task.id}: sem arquivos de trabalho"
    assert task.solution, f"{task.id}: sem solucao de referencia"
    assert set(task.frozen) <= set(task.work), f"{task.id}: congelado fora da fixture"
    # tudo compila
    for nome, conteudo in {**task.work, **task.solution}.items():
        ast.parse(conteudo, filename=nome)
    ast.parse(task.hidden, filename=f"test_harness_{task.id}.py")


@pytest.mark.parametrize("task", all_tasks(), ids=lambda t: t.id)
def test_oculto_importa_modulo_existente(task):
    """O teste oculto so pode importar modulos presentes na fixture/solucao."""
    disponiveis = {Path(n).stem for n in {**task.work, **task.solution}}
    externos = {"pytest", "inspect", "dataclasses"}
    importados: set[str] = set()
    for no in ast.walk(ast.parse(task.hidden)):
        if isinstance(no, ast.Import):
            importados.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            importados.add(no.module.split(".")[0])
    desconhecidos = importados - disponiveis - externos
    assert not desconhecidos, f"{task.id}: teste oculto importa {desconhecidos} (nao existe)"


def test_ids_unicos_e_filtro():
    ids = [t.id for t in all_tasks()]
    assert len(ids) == len(set(ids)) == len(TAREFAS)
    assert get_tasks([ids[0]])[0].id == ids[0]
    with pytest.raises(KeyError):
        get_tasks(["nao_existe"])


def test_sandbox_nega_comandos_perigosos():
    """O gerador do config de sandbox tem que negar a trindade perigosa."""
    permissoes = [{"action": "*", "resource": "*", "effect": "allow"}]
    from scripts.model_harness.runner import _DENY_SANDBOX

    assert {"shell", "bash", "webfetch", "task"} <= set(_DENY_SANDBOX)
    permissoes += [{"action": a, "resource": "*", "effect": "deny"} for a in _DENY_SANDBOX]
    # a ultima regra que casa deve ser o deny
    assert permissoes[-1]["effect"] == "deny"


def test_parse_stream_extrai_fatos():
    linhas = (
        '{"type":"step_start","part":{}}\n'
        '{"type":"tool_use","part":{"tool":"edit","state":{"input":{"path":"a.py"}}}}\n'
        '{"type":"tool_use","part":{"tool":"edit","state":{"input":{"path":"a.py"}}}}\n'
        '{"type":"tool_use","part":{"tool":"read","state":{"input":{"path":"b.py"}}}}\n'
        '{"type":"step_finish","part":{"tokens":{"input":100,"output":25}}}\n'
        "linha de log que nao e JSON\n"
        '{"type":"step_finish","part":{"tokens":{"input":10,"output":5}}}'
    )
    fatos = _parse_stream(linhas)
    assert fatos["steps"] == 1
    assert fatos["tools"] == {"edit": 2, "read": 1}
    assert fatos["files"] == ["a.py"]
    assert fatos["tok_in"] == 110
    assert fatos["tok_out"] == 30


def test_fora_do_sandbox_nao_tem_falso_positivo(tmp_path):
    """Relativo = sandbox-relativo; e /a/b nao pode 'conter' /a/bc."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    assert _fora_do_sandbox([], sandbox) == []
    assert _fora_do_sandbox(["alvo.py"], sandbox) == []
    assert _fora_do_sandbox([str(sandbox / "alvo.py")], sandbox) == []
    assert _fora_do_sandbox(["../fora.py"], sandbox) == ["../fora.py"]
    assert _fora_do_sandbox(["/etc/passwd"], sandbox) == ["/etc/passwd"]
    irmao = tmp_path / "sandboxx"
    assert _fora_do_sandbox([str(irmao / "x.py")], sandbox) == [str(irmao / "x.py")]


def test_sem_prefixo_remove_so_o_provider():
    # So o provider Ollama e' removido: nomes de outras origens passam intactos.
    assert sem_prefixo("ollama/qwen3.6:35b-a3b") == "qwen3.6:35b-a3b"
    assert sem_prefixo("qwen3.6:35b-a3b") == "qwen3.6:35b-a3b"
    assert sem_prefixo("openai/gpt-4o-mini") == "openai/gpt-4o-mini"


def test_proveniencia_tem_os_campos_e_degrada(monkeypatch):
    """O placar SEMPRE carrega runtime+digests; falha de rede nao derruba."""
    from scripts.model_harness import provenance

    monkeypatch.setattr(provenance, "_versao_de", lambda _c: "0.40.2")
    monkeypatch.setattr(
        provenance, "digests_ollama", lambda _m, timeout=15: {"qwen3.6:35b-a3b": "abc"}
    )
    amb = coletar(["ollama/qwen3.6:35b-a3b"])
    assert amb["ollama_version"] == "0.40.2"
    assert amb["model_digests"]["qwen3.6:35b-a3b"] == "abc"
    assert "capturado_em" in amb and "opencode_version" in amb

    monkeypatch.setattr(provenance, "_versao_de", lambda _c: None)
    monkeypatch.setattr(provenance, "digests_ollama", lambda _m, timeout=15: {})
    vazio = coletar(["ollama/nao-existe"])
    assert vazio["ollama_version"] is None
    assert vazio["model_digests"] == {}


def test_resumir_agrega_por_modelo():
    rs = [
        RunResult("ollama/a", "t1", 1, True, True, True, False, None, 10.0, 3, 100, 50),
        RunResult("ollama/a", "t2", 1, False, False, True, False, None, 20.0, 5, 100, 50),
        RunResult("ollama/b", "t1", 1, True, True, True, False, None, 5.0, 2, 40, 20),
    ]
    resumo = {s.model: s for s in resumir(rs)}
    assert resumo["ollama/a"].passed == 1
    assert resumo["ollama/a"].frozen_rate == 1.0
    assert resumo["ollama/a"].wall_s == 15.0
    assert resumo["ollama/b"].pass_rate == 1.0


def test_fontes_sem_segredos():
    """Guarda do gitleaks: nada de `sk-` nem nomes de campo sensiveis no pacote."""
    for arquivo in list(PACOTE.glob("*.py")) + [PACOTE.parent / "check_models.py"]:
        texto = arquivo.read_text(encoding="utf-8")
        assert "sk-" not in texto, f"{arquivo.name} contem valor com prefixo de segredo"
        assert "api_key" not in texto, f"{arquivo.name} contem nome de campo sensivel"
