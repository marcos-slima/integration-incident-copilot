#!/usr/bin/env python3
"""Doctor dos modelos locais: confere se o que a config AFIRMA e' verdade.

A config global do OpenCode declara cada modelo com `tools: true` e certa
responsividade. Isso envelhece: basta um `ollama pull` trocar a tag. Este
script checa, contra o Ollama REAL, os tres fatos que sustentam a escolha:

1. EXISTE      -> o modelo esta instalado (senao a config mente).
2. TOOL CALLING -> devolve `tool_calls` com nome + argumentos JSON validos
                   (a capacidade que o OpenCode exige; a suite de
                   diagnostico nao mede isso).
3. VELOCIDADE  -> tokens/s de geracao, medidos num prompt de codigo.

`gpt-oss:120b` fica fora por decisao de cenario (65 GB). Sai com codigo 1
se qualquer modelo listado faltar ou falhar o tool calling - serve de
portao antes de confiar no placar do harness.

Uso:
    python scripts/check_models.py
    python scripts/check_models.py --models qwen3.6:35b-a3b,qwen3-coder:latest
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.model_harness.provenance import coletar

OLLAMA = "http://localhost:11434"
DEFAULT_MODELS = [
    "qwen3.6:35b-a3b",
    "qwen3-coder:latest",
    "qwen3-coder-next:latest",
]
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Retorna o clima atual de uma cidade.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]
_PROMPT = "Escreva uma funcao Python que soma dois numeros e retorna o resultado."


def _post(rota: str, payload: dict, timeout: int = 300) -> dict:
    req = urllib.request.Request(
        OLLAMA + rota,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _instalados() -> dict[str, str]:
    """Nome -> digest dos modelos locais (a prova de que os bytes nao mudaram)."""
    try:
        return {m["name"]: m.get("digest", "?") for m in _post_get("/api/tags").get("models", [])}
    except urllib.error.URLError:
        return {}


def _post_get(rota: str, timeout: int = 15) -> dict:
    with urllib.request.urlopen(OLLAMA + rota, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def checar_tools(modelo: str) -> bool:
    data = _post(
        "/api/chat",
        {
            "model": modelo,
            "messages": [
                {"role": "user", "content": "Qual o clima em Sao Paulo? Use a ferramenta."}
            ],
            "tools": _TOOLS,
            "stream": False,
            "options": {"temperature": 0.2},
        },
    )
    calls = (data.get("message") or {}).get("tool_calls") or []
    if not calls:
        return False
    try:
        fn = calls[0]["function"]
        args = fn["arguments"]
        if isinstance(args, str):
            args = json.loads(args)
        return fn["name"] == "get_weather" and "city" in args
    except (KeyError, TypeError, json.JSONDecodeError):
        return False


def medir_latencia(modelo: str) -> float:
    data = _post(
        "/api/generate",
        {
            "model": modelo,
            "prompt": _PROMPT,
            "stream": False,
            "options": {"num_predict": 128, "temperature": 0.2},
        },
    )
    ed = data.get("eval_duration", 0) / 1_000_000_000
    return round(data.get("eval_count", 0) / ed, 1) if ed else 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", help="lista separada por virgula")
    args = parser.parse_args(argv)
    modelos = [m.strip() for m in (args.models or ",".join(DEFAULT_MODELS)).split(",") if m.strip()]

    try:
        instalados = _instalados()
    except urllib.error.URLError:
        print("ERRO: Ollama nao respondeu em http://localhost:11434", file=sys.stderr)
        return 2
    if not instalados:
        print("ERRO: Ollama nao respondeu em http://localhost:11434", file=sys.stderr)
        return 2

    print(f"{'modelo':26s}{'instalado':>10}{'tool-calling':>14}{'tok/s':>9}  digest")
    falhou = False
    for modelo in modelos:
        presente = modelo in instalados
        if not presente:
            print(f"{modelo:26s}{'NAO':>10}{'-':>14}{'-':>9}  -")
            falhou = True
            continue
        t0 = time.time()
        tools_ok = checar_tools(modelo)
        tok_s = medir_latencia(modelo)
        digest = instalados.get(modelo, "?")[:12]
        print(
            f"{modelo:26s}{'sim':>10}{'OK' if tools_ok else 'FALHOU':>14}{tok_s:>9}"
            f"  {digest}    ({round(time.time() - t0)}s)"
        )
        if not tools_ok:
            falhou = True

    amb = coletar(modelos)
    print(
        f"\nambiente: ollama {amb['ollama_version'] or '?'} | "
        f"opencode {amb['opencode_version'] or '?'}"
    )
    print(
        "\n" + ("TUDO OK" if not falhou else "ATENCAO: algum modelo faltou ou falhou tool calling")
    )
    return 1 if falhou else 0


if __name__ == "__main__":
    sys.exit(main())
