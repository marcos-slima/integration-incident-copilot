"""Catalogo oficial de cStat (MOC 7.0 Anexo I, secao 4.4), extraido por
scripts/extract_cstat.py. Da ao fato F-cStat o significado oficial, para o
LLM nao explicar o codigo de memoria (medicao cega 2026-11-13, D8)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG = Path(__file__).resolve().parents[3] / "data" / "corpus" / "cstat_moc7_anexo_i.json"
TP_AMB = {"1": "producao", "2": "homologacao"}
# cStat cujo protocolo traz nProt e prova o resultado (autorizacao ou denegacao).
COM_PROTOCOLO = {"100", "150", "110", "301", "302", "303"}


@lru_cache(maxsize=1)
def _catalog() -> dict[str, Any]:
    if not CATALOG.exists():
        return {}
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def describe(cstat: str | None) -> dict[str, Any] | None:
    """{"text", "table", "page", "source_id"} do codigo, ou None se ausente."""
    cat = _catalog()
    entry = (cat.get("codes") or {}).get(cstat or "")
    if not entry:
        return None
    return {
        "text": entry["texts"][0],
        "table": entry["table"],
        "page": entry["page"],
        "source_id": cat["source_id"],
    }
