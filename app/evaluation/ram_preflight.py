"""Preflight de RAM do harness de promptfoo.

Por que isto NAO esta dentro do shell script: a aritmetica que decide se a
suite roda e' o unico ponto onde um erro custa 48G de RAM e a confianca de
quem esta debugando. Dentro de um heredoc ela so pode ser verificada
carregando o modelo de verdade, o que e' caro, lento e -- pior -- nao e'
teste. Aqui o nucleo e' uma funcao pura e o resto e' I/O fino por cima.

O script consome isto por `python -m app.evaluation.ram_preflight`; a suite
cobre o nucleo em tests/test_ram_preflight.py.

Saidas (contrato com scripts/promptfoo_remote.sh):
    0  pode rodar            4  nao cabe (bloqueio deliberado)
    5  passou em modo preflight-only (nada foi carregado)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

GIB = 2**30

EXIT_OK = 0
EXIT_BLOCKED = 4
EXIT_PREFLIGHT_OK = 5

RESERVE_ENV = "EVAL_KV_RESERVE_GIB"
PREFLIGHT_ONLY_FLAG = "preflight-only"

# Ausencia de /proc/meminfo (Ollama remoto), /api/ps sem "size" ou python
# sem urllib: o preflight sai 0 em silencio. Medir infraestrutura nao pode
# ser a razao de um eval nao rodar -- e um gate que mente e' pior que
# nenhum gate.
FAIL_OPEN_EXIT = EXIT_OK


def _gib(value: int) -> float:
    return round(value / GIB, 1)


def resident_map(
    models: Sequence[Mapping[str, Any]],
    tags: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, int]:
    """Tamanho ocupado em RAM por modelo resident, por nome.

    `/api/ps` manda no valor: medido em 2026-09-28, `/api/ps` acusava 49.6G
    contra 48.2G de `/api/tags` para o mesmo qwen3-strict. A diferenca e' o
    buffer de compute, que so existe em RAM com o modelo de pe -- contar o
    arquivo subestima o occupant real. O fallback para `/api/tags` existe
    so para Ollama antigo que nao devolve "size" em /api/ps.
    """
    tags = tags or {}
    return {
        model["name"]: int(model.get("size") or tags.get(model["name"], {}).get("size") or 0)
        for model in models
    }


def required_bytes(
    resident: Mapping[str, int],
    target: str,
    target_size: int,
    reserve_bytes: int,
) -> int:
    """RAM total necessaria: resident + alvo (se preciso) + reserva de KV.

    O invariante: um alvo que JA esta resident nao entra duas vezes. Sem
    isso, `loaded` (soma de /api/ps) e `target_size` (de /api/tags)
    descrevem o mesmo modelo em dois lugares -- um retake de caso ou o
    resume de uma suite interrompida reprovava por conta propria, e a dica
    mandava `ollama stop` do modelo que o caller estava pedindo para usar.
    """
    loaded = sum(resident.values())
    if target in resident:
        target_size = 0
    return loaded + target_size + reserve_bytes


def unload_hint(resident: Mapping[str, int], target: str) -> str:
    """O que descarregar, ja excluindo o alvo.

    O alvo e' o unico modelo que o caller nao pode derrubar sem matar o que
    ele esta tentando rodar, entao nunca entra na sugestao -- mesmo sendo o
    maior occupant. Sugerir `ollama stop <alvo>` e' uma dica que nao libera
    RAM e transfere a culpa do OOM para o preflight.
    """
    others = sorted(
        ((name, size) for name, size in resident.items() if name != target),
        key=lambda item: item[1],
        reverse=True,
    )
    if others:
        return f"ollama stop {others[0][0]}"
    return f"nada a descarregar: reduza OLLAMA_CONTEXT_LENGTH ou {RESERVE_ENV}"


def say(message: str) -> None:
    """stdout e' a resposta do promptfoo; diagnostico vai por stderr."""
    print(f"[ram] {message}", file=sys.stderr)


def _get(host: str, path: str) -> Any:
    with urllib.request.urlopen(f"{host}{path}", timeout=3) as response:
        return json.load(response)


def _measure() -> tuple[list[Mapping[str, Any]], dict[str, Mapping[str, Any]], int]:
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    if "://" not in host:
        host = "http://" + host
    models = _get(host, "/api/ps").get("models", [])
    tags = {m["name"]: m for m in _get(host, "/api/tags").get("models", [])}
    with open("/proc/meminfo") as handle:
        available = next(
            int(line.split()[1]) * 1024 for line in handle if line.startswith("MemAvailable:")
        )
    return list(models), tags, available


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    target = argv[0]
    reserve_gib = float(argv[1]) if len(argv) > 1 else 8.0
    preflight_only = PREFLIGHT_ONLY_FLAG in argv[1:]

    try:
        models, tags, available = _measure()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Falha de MEDICAO nao bloqueia: sem /proc/meminfo, Ollama remoto
        # sem /api/ps, resposta sem "models" -- a suite roda sem preflight.
        # A excecao e' estreita de proposito: `except Exception` tambem
        # engoliria um bug nosso (TypeError na propria conta) e devolveria
        # "pode rodar" com silencio -- gate que mente e' pior que sem gate.
        # Qualquer coisa fora desta lista escapa com traceback e o shell
        # segue sem preflight, que e' o mesmo desfecho mas visivel.
        say(f"nao mediu ({type(exc).__name__}: {exc}) - preflight sem opiniao")
        return FAIL_OPEN_EXIT

    target_size = tags.get(target, {}).get("size")
    if target_size is None:
        say(f"'{target}' nao esta instalado neste Ollama - o provider quebraria com 404")
        return EXIT_BLOCKED

    resident = resident_map(models, tags)
    resident_total = sum(resident.values())
    reserve = int(reserve_gib * GIB)
    total = required_bytes(resident, target, int(target_size), reserve)

    if resident:
        listed = ", ".join(f"{name} ({_gib(size)}G)" for name, size in sorted(resident.items()))
        # O total so interessa com mais de um occupant: com um so, o numero
        # ja esta no proprio item da lista.
        total_note = f" - total {_gib(resident_total)}G" if len(resident) > 1 else ""
        say(f"ja resident: {listed}{total_note}")

    if target in resident:
        need_note = f"{target} ja resident, conta dentro dos {_gib(resident_total)}G acima"
    else:
        need_note = f"{target} = {_gib(int(target_size))}G"
    say(
        f"disponivel {_gib(available)}G | precisa {_gib(total)}G "
        f"({need_note} + {reserve_gib:.0f}G de reserva p/ KV cache)"
    )

    if preflight_only:
        say("PREFLIGHT ONLY: nada foi carregado, nenhuma chamada de LLM feita")
        return EXIT_PREFLIGHT_OK if total <= available else EXIT_BLOCKED

    if total > available:
        say(
            f"PREFLIGHT FAIL: nao cabe em {_gib(available)}G. "
            f"Descarregue antes: {unload_hint(resident, target)}"
        )
        return EXIT_BLOCKED

    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
