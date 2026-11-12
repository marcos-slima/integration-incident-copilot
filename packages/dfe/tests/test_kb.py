"""P3: base de conhecimento fiscal (dfe_copilot.kb) com Qdrant em memoria.

Embeddings falsos e deterministicos: o teste mede o contrato (metadados,
filtro antes da busca, abstencao, revogado fora), nao a qualidade semantica.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from dfe_copilot import kb
from qdrant_client import QdrantClient
from qdrant_client.models import SparseVector

VOCAB = ["duplicidade", "204", "chave", "protocolo", "schema", "rejeicao", "timeout", "nfe"]


def dense(texts: list[str]) -> list[list[float]]:
    out = []
    for t in texts:
        low = t.lower()
        vec = [float(low.count(w)) + 0.01 for w in VOCAB]
        out.append(vec)
    return out


def sparse(text: str) -> SparseVector:
    low = text.lower()
    idx = [i for i, w in enumerate(VOCAB) if w in low]
    return SparseVector(indices=idx or [0], values=[1.0] * (len(idx) or 1))


def fake_pdf_pages(path: Path) -> list[dict]:
    return [
        {"text": "Regra 2B08-20: rejeicao 204 duplicidade de NF-e pela chave.", "page_number": 22}
    ]


@pytest.fixture
def client() -> QdrantClient:
    return QdrantClient(":memory:")


def test_corpus_real_tem_metadados_obrigatorios() -> None:
    chunks = kb.load_corpus(pdf_pages=fake_pdf_pages)
    assert chunks
    for c in chunks:
        for key in ("source_id", "authority_class", "content_sha256", "model", "status", "file"):
            assert c.payload.get(key) is not None, (key, c.payload["file"])
    regra = [c for c in chunks if c.payload["file"] == "regra_2B08-20.md"]
    assert regra and regra[0].payload["cstat"] == "204"
    assert regra[0].payload["model"] == ["55", "65"]
    assert regra[0].payload["reviewed_by"] is None


def test_hash_divergente_recusa(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    shutil.copytree(kb.CORPUS_DIR, corpus)
    pdf = corpus / "NT_2018.005_v1.51.pdf"
    pdf.write_bytes(pdf.read_bytes() + b"x")
    with pytest.raises(ValueError, match="hash divergente"):
        kb.load_corpus(corpus, pdf_pages=fake_pdf_pages)


def test_ingest_idempotente(client: QdrantClient) -> None:
    chunks = kb.load_corpus(pdf_pages=fake_pdf_pages)
    n = kb.ingest(client, dense, sparse, chunks)
    kb.ingest(client, dense, sparse, chunks)
    assert client.count(kb.COLLECTION).count == n


def test_busca_hibrida_filtrada_traz_a_regra(client: QdrantClient) -> None:
    kb.ingest(client, dense, sparse, kb.load_corpus(pdf_pages=fake_pdf_pages))
    hits = kb.search(client, "duplicidade 204 chave", dense, sparse, cstat="204")
    assert hits and all(h["cstat"] == "204" for h in hits)
    assert {h["trust_level"] for h in hits} <= {"rule_reference", "retrieved_document"}
    assert all(h["evidence_id"].startswith("kb:") for h in hits)


def test_modelo_fora_do_escopo_abstem(client: QdrantClient) -> None:
    kb.ingest(client, dense, sparse, kb.load_corpus(pdf_pages=fake_pdf_pages))
    assert kb.search(client, "duplicidade", dense, sparse, model="57") == []


def test_revogado_nunca_volta(client: QdrantClient, tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    shutil.copytree(kb.CORPUS_DIR, corpus)
    (corpus / "revogada.md").write_text(
        '---\nsource_id: X\ncstat: "204"\nmodel: ["55"]\nstatus: revogado\nauthority_class: oficial_gov\n---\n\n'
        "duplicidade 204 chave revogada\n",
        encoding="utf-8",
    )
    kb.ingest(client, dense, sparse, kb.load_corpus(corpus, pdf_pages=fake_pdf_pages))
    hits = kb.search(client, "duplicidade 204 chave revogada", dense, sparse, cstat="204")
    assert hits and all(h["status"] != "revogado" for h in hits)


def test_sem_colecao_abstem(client: QdrantClient) -> None:
    assert kb.search(client, "qualquer", dense, sparse) == []


def test_manifesto_e_corpus_consistentes() -> None:
    m = json.loads((kb.CORPUS_DIR / "manifest.json").read_text(encoding="utf-8"))
    for doc in m["documents"]:
        if doc["stored"]:
            assert (
                hashlib.sha256((kb.CORPUS_DIR / doc["file"]).read_bytes()).hexdigest()
                == doc["content_sha256"]
            )
