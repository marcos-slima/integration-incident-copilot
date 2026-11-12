"""Corpus fiscal v0 (T03/T05): manifesto, hashes e metadados da regra curada."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[1] / "data" / "corpus"
MANIFEST = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))


def test_documentos_versionados_conferem_hash() -> None:
    for doc in MANIFEST["documents"]:
        if doc["stored"]:
            got = hashlib.sha256((CORPUS / doc["file"]).read_bytes()).hexdigest()
            assert got == doc["content_sha256"], doc["source_id"]


def test_metadados_obrigatorios() -> None:
    campos = {"source_id", "publisher", "document_type", "version", "canonical_url",
              "authority_class", "content_sha256", "license_scope"}  # fmt: skip
    assert all(campos <= set(d) for d in MANIFEST["documents"])


def test_regra_curada_cita_fonte_e_nao_esta_revisada() -> None:
    texto = (CORPUS / "regra_2B08-20.md").read_text(encoding="utf-8")
    assert "source_id: NT_2018.005_v1.51" in texto and 'cstat: "204"' in texto
    assert "reviewed_by: null" in texto
