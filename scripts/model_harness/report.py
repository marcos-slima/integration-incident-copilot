"""Agregacao e impressao do placar do harness de refactoring."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from scripts.model_harness.runner import RunResult


@dataclass
class ModelSummary:
    """Resumo por modelo: acerto, precisao e velocidade agregados."""

    model: str
    n: int
    passed: int
    frozen_ok: int
    wall_s: float
    steps: float
    tok_s: float
    tools: dict[str, int]

    @property
    def pass_rate(self) -> float:
        return self.passed / self.n if self.n else 0.0

    @property
    def frozen_rate(self) -> float:
        return self.frozen_ok / self.n if self.n else 0.0


def resumir(results: list[RunResult]) -> list[ModelSummary]:
    """Agrega por modelo, na ordem de primeira aparicao."""
    grupos: dict[str, list[RunResult]] = defaultdict(list)
    ordem: list[str] = []
    for r in results:
        if r.model not in grupos:
            ordem.append(r.model)
        grupos[r.model].append(r)

    saida: list[ModelSummary] = []
    for modelo in ordem:
        rs = grupos[modelo]
        n = len(rs)
        ferramentas: dict[str, int] = defaultdict(int)
        for r in rs:
            for k, v in r.tools.items():
                ferramentas[k] += v
        wall = sum(r.wall_s for r in rs)
        tok_out = sum(r.tokens_out for r in rs)
        saida.append(
            ModelSummary(
                model=modelo,
                n=n,
                passed=sum(1 for r in rs if r.passed),
                frozen_ok=sum(1 for r in rs if r.frozen_ok),
                wall_s=round(wall / n, 1) if n else 0.0,
                steps=round(sum(r.steps for r in rs) / n, 1) if n else 0.0,
                tok_s=round(tok_out / wall, 1) if wall > 0 else 0.0,
                tools=dict(sorted(ferramentas.items())),
            )
        )
    return saida


def imprimir(results: list[RunResult]) -> None:
    """Imprime a matriz tarefa x modelo e o resumo por modelo."""
    modelos: list[str] = []
    tarefas: list[str] = []
    por_par: dict[tuple[str, str], list[RunResult]] = defaultdict(list)
    for r in results:
        if r.model not in modelos:
            modelos.append(r.model)
        if r.task not in tarefas:
            tarefas.append(r.task)
        por_par[(r.model, r.task)].append(r)

    larg = max((len(m) for m in modelos), default=10) + 2
    print("\n=== matriz: acertos por (modelo x tarefa) ===")
    cabecalho = "tarefa".ljust(20) + "".join(m.rjust(larg) for m in modelos)
    print(cabecalho)
    for tarefa in tarefas:
        linha = tarefa.ljust(20)
        for modelo in modelos:
            rs = por_par.get((modelo, tarefa), [])
            if not rs:
                celula = "-"
            else:
                acertos = sum(1 for r in rs if r.passed)
                marca = "OK" if all(r.passed for r in rs) else ("X" if acertos == 0 else "~")
                celula = f"{acertos}/{len(rs)} {marca}"
            linha += celula.rjust(larg)
        print(linha)

    print("\n=== resumo por modelo ===")
    print(f"{'modelo':32s}{'acerto':>10}{'precisao':>12}{'wall(s)':>10}{'passos':>9}{'tok/s':>8}")
    for s in resumir(results):
        print(
            f"{s.model:32s}{f'{s.passed}/{s.n}':>10}{f'{s.frozen_rate:.0%}':>12}"
            f"{s.wall_s:>10}{s.steps:>9}{s.tok_s:>8}"
        )

    falhas = [r for r in results if not r.passed]
    if falhas:
        print("\n=== falhas (o que quebrou) ===")
        for r in falhas:
            motivo = "timeout" if r.timed_out else ("erro" if r.error else "assert")
            marcas = []
            if not r.frozen_ok and not r.timed_out:
                marcas.append("congelado violado")
            if r.escaped:
                marcas.append("ESCAPOU do sandbox")
            extra = f" [{', '.join(marcas)}]" if marcas else ""
            print(f"- {r.model} / {r.task} (#{r.repeat}): {motivo}{extra}")
            if r.pytest_tail:
                print("    " + r.pytest_tail.splitlines()[-1][:110])
