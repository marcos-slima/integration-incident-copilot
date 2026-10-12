"""Proveniencia do ambiente: versoes e digests que TODO placar deve carregar.

Motivo (2026-10-11). Uma decisao deste repositorio (DA-8) rejeitou o modelo
`qwen3.6:35b-a3b` por "falha determinista de JSON estruturado". Ao verificar
na fonte, essa falha era o bug do Ollama #10929 ("Ollama produces invalid JSON
when using thinking mode with structured output"), aberto em maio/2025 e
corrigido no v0.34.4 - ou seja, era RUNTIME, nao modelo. Um placar que nao
grava a versao do runtime nao permite separar as duas coisas; e um placar que
nao grava o digest do modelo nao detecta um `ollama pull` que trocou os bytes
sob a mesma tag. Este modulo captura os dois para viajar junto do resultado.

Degrada com elegancia: campo vira `None`/`{}` quando o binario ou o servidor
nao respondem - proveniencia ausente e' melhor que placar que nao roda.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import UTC, datetime

OLLAMA = "http://localhost:11434"


def sem_prefixo(modelo: str) -> str:
    """`ollama/qwen3.6:35b-a3b` -> `qwen3.6:35b-a3b` (o nome que o Ollama usa)."""
    return modelo.split("/", 1)[1] if modelo.startswith("ollama/") else modelo


def _versao_de(comando: str) -> str | None:
    """Versao de um binario via `--version`; `None` se ausente ou falhar."""
    binario = shutil.which(comando)
    if not binario:
        return None
    try:
        proc = subprocess.run(
            [binario, "--version"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return ((proc.stdout or "") + (proc.stderr or "")).strip() or None


def digests_ollama(modelos: list[str], timeout: int = 15) -> dict[str, str]:
    """Mapa nome-do-Ollama -> digest, so para os modelos existentes localmente."""
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=timeout) as resp:
            locais = {
                m["name"]: m.get("digest", "?")
                for m in json.loads(resp.read().decode()).get("models", [])
            }
    except (OSError, urllib.error.URLError, json.JSONDecodeError, KeyError):
        return {}
    return {sem_prefixo(m): locais[sem_prefixo(m)] for m in modelos if sem_prefixo(m) in locais}


def coletar(modelos: list[str]) -> dict:
    """Proveniencia completa do ambiente para anexar ao JSON do placar."""
    return {
        "capturado_em": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "ollama_version": _versao_de("ollama"),
        "opencode_version": _versao_de("opencode"),
        "model_digests": digests_ollama(modelos),
    }
