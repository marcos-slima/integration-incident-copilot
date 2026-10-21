"""Helpers puros de `app/rag/ingest.py` (IIC-AUD-108).

Cobrem o que a ingestao decide sem Qdrant/Ollama: descoberta de arquivos,
estado incremental, hash, ids deterministicos e o payload montado por
`embed_and_upsert` (denso e hibrido). Sem rede: o modelo esparso e' stub.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from app.rag import ingest


def test_resolve_source_dir_prefere_primario_com_arquivos(tmp_path):
    primary, fallback = tmp_path / "p", tmp_path / "f"
    primary.mkdir()
    (primary / "a.md").write_text("x", encoding="utf-8")
    cfg = {"primary_dir": primary, "fallback_dir": fallback}
    assert ingest.resolve_source_dir(cfg) == primary


def test_resolve_source_dir_usa_fallback_quando_vazio(tmp_path):
    primary, fallback = tmp_path / "p", tmp_path / "f"
    primary.mkdir()
    (primary / "ignorado.bin").write_text("x", encoding="utf-8")
    assert ingest.resolve_source_dir({"primary_dir": primary, "fallback_dir": fallback}) == fallback
    assert ingest.resolve_source_dir({"primary_dir": primary, "fallback_dir": None}) == primary


def test_find_files_filtra_sufixo_e_excludes(tmp_path):
    assert ingest.find_files(tmp_path / "nao-existe", []) == []
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "sub" / "B.md").write_text("x", encoding="utf-8")
    (tmp_path / "c.bin").write_text("x", encoding="utf-8")
    achados = ingest.find_files(tmp_path, [])
    assert [p.name for p in achados] == ["a.md", "B.md"]
    assert [p.name for p in ingest.find_files(tmp_path, ["SUB"])] == ["a.md"]


def test_state_roundtrip_e_formato_legado(tmp_path):
    state = tmp_path / "s" / "state.json"
    assert ingest.load_state(state) == {}
    ingest.save_state(state, {"h:a.md": "a.md"})
    assert ingest.load_state(state) == {"h:a.md": "a.md"}
    state.write_text(json.dumps(["x.md", "y.md"]), encoding="utf-8")
    assert ingest.load_state(state) == {"x.md": "x.md", "y.md": "y.md"}


def test_file_hash_e_state_key(tmp_path):
    f = tmp_path / "doc.md"
    f.write_bytes(b"conteudo")
    assert ingest.file_hash(f) == hashlib.sha256(b"conteudo").hexdigest()
    assert ingest._state_key(f) == f"{hashlib.sha256(b'conteudo').hexdigest()}:doc.md"
    assert ingest.extract_text(f) == "conteudo"


def test_ids_deterministicos_dependem_so_do_caminho():
    a = ingest.deterministic_document_id("docs/a.md")
    assert a == ingest.deterministic_document_id("docs/a.md")
    assert a != ingest.deterministic_document_id("docs/b.md")
    assert ingest.deterministic_point_id(a, 0) != ingest.deterministic_point_id(a, 1)
    assert ingest.deterministic_point_id(a, 3) == ingest.deterministic_point_id(a, 3)


class _Emb:
    def embed_documents(self, texts):
        return [[float(len(t))] for t in texts]

    def embed_query(self, _t):
        return [0.0] * 7


class _Client:
    def __init__(self):
        self.calls = []

    def upsert(self, collection_name, points):
        self.calls.append((collection_name, points))


def test_probe_vector_size():
    assert ingest.probe_vector_size(_Emb()) == 7


def test_embed_and_upsert_denso_payload_e_lotes(monkeypatch):
    monkeypatch.setattr(ingest, "EMBED_BATCH_SIZE", 2)
    client = _Client()
    chunks = [{"text": "aa"}, {"text": "bbb", "page_number": 2}, {"text": "c"}]
    meta = {"document_id": "doc-1", "filename": "a.md", "category": "integration"}
    ingest.embed_and_upsert(client, "col", _Emb(), chunks, "a.md", hybrid=False, doc_meta=meta)

    assert [len(p) for _, p in client.calls] == [2, 1]
    pontos = [p for _, lote in client.calls for p in lote]
    assert [p.payload["chunk_index"] for p in pontos] == [0, 1, 2]
    assert pontos[1].payload["page_number"] == 2
    assert pontos[0].vector == [2.0]
    assert pontos[2].id == ingest.deterministic_point_id("doc-1", 2)
    assert pontos[0].payload["title"] == "a"


def test_embed_and_upsert_hibrido_usa_vetor_esparso(monkeypatch):
    sparse = SimpleNamespace(
        indices=SimpleNamespace(tolist=lambda: [3]), values=SimpleNamespace(tolist=lambda: [0.5])
    )
    modelo = SimpleNamespace(embed=lambda textos: iter([sparse for _ in textos]))
    monkeypatch.setattr(ingest, "_get_sparse_model", lambda: modelo)
    client = _Client()
    ingest.embed_and_upsert(
        client, "col", _Emb(), [{"text": "x"}], "a.md", hybrid=True, doc_meta={"document_id": "d"}
    )
    vetor = client.calls[0][1][0].vector
    assert vetor["dense"] == [1.0]
    assert vetor["sparse"].indices == [3]
    assert vetor["sparse"].values == [0.5]
