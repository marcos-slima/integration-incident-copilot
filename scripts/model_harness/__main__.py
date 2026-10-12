"""CLI do harness de refactoring.

Uso:
    python -m scripts.model_harness tasks
    python -m scripts.model_harness run --models ollama/qwen3.6:35b-a3b,ollama/qwen3-coder:latest
    python -m scripts.model_harness run --tasks bug_mediana,precisao_frozen --repeats 3
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from scripts.model_harness.provenance import coletar
from scripts.model_harness.report import imprimir
from scripts.model_harness.runner import RunResult, run_one
from scripts.model_harness.tasks import TAREFAS, all_tasks, get_tasks

# Candidatos do ambiente local. `gpt-oss:120b` fica FORA de proposito:
# 65 GB e TTFT ~15s o tornam inaplicavel ao cenario (decisao do usuario,
# 2026-10-11).
DEFAULT_MODELS = [
    "ollama/qwen3.6:35b-a3b",
    "ollama/qwen3-coder:latest",
    "ollama/qwen3-coder-next:latest",
    "ollama/qwen3.8:27b",
]


def _cmd_tasks(_args: argparse.Namespace | None = None) -> int:
    print(f"{'id':20s}{'timeout':>9}  congelados          titulo")
    for t in all_tasks():
        congelados = ",".join(t.frozen) or "-"
        print(f"{t.id:20s}{t.timeout_s:>9}  {congelados:18s} {t.title}")
    print(f"\n{len(TAREFAS)} tarefas.")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    modelos = [m.strip() for m in (args.models or ",".join(DEFAULT_MODELS)).split(",") if m.strip()]
    tarefas = get_tasks(
        [t.strip() for t in args.tasks.split(",") if t.strip()] if args.tasks else None
    )

    ambiente = coletar(modelos)
    print(f"modelos : {', '.join(modelos)}")
    print(f"tarefas : {', '.join(t.id for t in tarefas)}")
    print(
        f"repeticoes: {args.repeats} | timeout: {args.timeout}s | bash: "
        f"{'LIBERADO' if args.allow_bash else 'negado'}"
    )
    # Proveniencia obrigatoria: sem ela nao da' para separar "modelo ruim" de
    # "runtime bugado" (licao do bug Ollama #10929 vs DA-8, 2026-10-11).
    print(
        f"ambiente: ollama {ambiente['ollama_version'] or '?'} | "
        f"opencode {ambiente['opencode_version'] or '?'}"
    )
    if not ambiente["model_digests"]:
        print("AVISO: nao consegui ler digests do Ollama - placar sem prova de bytes")
    if not args.allow_bash:
        print("(sandbox nega shell/webfetch/task -> mede EDICAO pura, com seguranca)")

    resultados: list[RunResult] = []
    total = len(modelos) * len(tarefas) * args.repeats
    i = 0
    for modelo in modelos:
        for tarefa in tarefas:
            for rep in range(1, args.repeats + 1):
                i += 1
                print(f"[{i}/{total}] {modelo} / {tarefa.id} (#{rep}) ...", end=" ", flush=True)
                r = run_one(modelo, tarefa, rep, timeout=args.timeout, allow_bash=args.allow_bash)
                marca = "OK" if r.passed else "FALHOU"
                print(f"{marca}  {r.wall_s}s  {r.steps} passos  {r.tok_s} tok/s", flush=True)
                resultados.append(r)

    imprimir(resultados)

    if args.out:
        destino = Path(args.out)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(
                {
                    "gerado_em": datetime.now(tz=UTC).isoformat(timespec="seconds"),
                    "repeticoes": args.repeats,
                    "modelos": modelos,
                    "tarefas": [t.id for t in tarefas],
                    "ambiente": ambiente,
                    "resultados": [r.to_dict() for r in resultados],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"\nJSON salvo em {destino}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="model_harness", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    tarefas = sub.add_parser("tasks", help="lista as tarefas e seus contratos")
    tarefas.set_defaults(func=_cmd_tasks)

    run = sub.add_parser("run", help="executa o placar modelo x tarefa")
    run.add_argument("--models", help="lista separada por virgula (provider/modelo)")
    run.add_argument("--tasks", help="ids separados por virgula (default: todas)")
    run.add_argument("--repeats", type=int, default=1, help="repeticoes por par (estabilidade)")
    run.add_argument("--timeout", type=int, default=None, help="timeout por execucao, em segundos")
    run.add_argument(
        "--allow-bash",
        action="store_true",
        help="reabre bash no sandbox (mede loop completo; assume risco)",
    )
    run.add_argument("--out", help="caminho do JSON de resultados")
    run.set_defaults(func=_cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
