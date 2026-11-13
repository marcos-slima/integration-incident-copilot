"""Extrai a tabela oficial de cStat do MOC 7.0 Anexo I (secao 4.4) para
data/corpus/cstat_moc7_anexo_i.json (catalogo determinístico, versionado).

    PYTHONPATH=src:../.. uv run --project ../.. python scripts/extract_cstat.py

O PDF (4,1 MB) nao e' versionado: baixe pela URL do manifest.json para
data/corpus/MOC_7.0_Anexo_I.pdf. O hash e' conferido antes da extracao.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dfe_copilot.kb import _pypdf_pages

CORPUS = ROOT / "data" / "corpus"
OUT = CORPUS / "cstat_moc7_anexo_i.json"
TABELAS = {"1": "resultado", "2": "rejeicao", "3": "denegacao"}
_HEAD = re.compile(r"^\s*4\.4\.(\d)\.\s")
_START = re.compile(r"4\.4\.1\. Tabela de Códigos de Resultado de Processamento \(cStat\)\s*\n")
_LINE = re.compile(r"^\s*(\d{3})\s+(\S.*?)\s*$")


def extract(pdf: Path) -> dict:
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    doc = next(d for d in manifest["documents"] if d["source_id"] == "MOC_7.0_Anexo_I")
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    if sha != doc["content_sha256"]:
        raise SystemExit(f"hash divergente: {sha}")
    pages = _pypdf_pages(pdf)
    start = next(i for i, p in enumerate(pages) if _START.search(p["text"]))
    codes: dict[str, dict] = {}
    tabela = None
    for p in pages[start:]:
        for line in p["text"].splitlines():
            h = _HEAD.match(line)
            if h:
                tabela = TABELAS.get(h.group(1))
            m = _LINE.match(line)
            if m and tabela:
                code, text = m.groups()
                entry = codes.setdefault(
                    code, {"texts": [], "page": p["page_number"], "table": tabela}
                )
                if text not in entry["texts"]:
                    entry["texts"].append(text)
    return {
        "source_id": doc["source_id"],
        "document": "MOC 7.0 Anexo I - secao 4.4 (tabelas de cStat)",
        "canonical_url": doc["canonical_url"],
        "content_sha256": sha,
        "extracted_by": "scripts/extract_cstat.py (pypdf, 1a linha de cada codigo)",
        "notes": "Texto pode vir cortado na quebra de linha do PDF; codigos com mais de um texto listam todos.",
        "codes": dict(sorted(codes.items())),
    }


def main() -> None:
    data = extract(CORPUS / "MOC_7.0_Anexo_I.pdf")
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(data['codes'])} codigos -> {OUT.name}")


if __name__ == "__main__":
    main()
