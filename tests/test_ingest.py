"""Testes de app/rag/ingest.py - so as funcoes puras de ID e o upsert
(sem Qdrant/Ollama/fastembed reais: embeddings e client sao fakes,
mesmo espirito de tests/test_queue.py).

Avaliacao externa (qualidade, item 25): "delete-before-upsert em
ingest.py contradiz a arquitetura documentada de ingestao idempotente
via ID deterministico - remover o delete previo". Corrigido junto com
uma mudanca no calculo do ID: deterministic_document_id() passou a
depender so de `source` (caminho relativo), nao mais do hash do
conteudo do arquivo - e isso, nao so a remocao do delete, e o que faz
o upsert idempotente de verdade (ver docstring da funcao em
app/rag/ingest.py). Estes testes provam que:
1. o ID nao muda quando o CONTEUDO do arquivo muda (so quando o
   caminho/source muda);
2. reingerir o mesmo `source` com conteudo diferente gera os MESMOS
   point ids por indice de chunk - ou seja, upsert sobrescreve no
   lugar, sem precisar de delete previo nem deixar pontos orfaos para
   os indices que continuam existindo."""

from app.rag.ingest import deterministic_document_id, deterministic_point_id, embed_and_upsert


def test_deterministic_document_id_stable_for_same_source():
    assert deterministic_document_id("guia/erro-idoc.md") == deterministic_document_id(
        "guia/erro-idoc.md"
    )


def test_deterministic_document_id_differs_for_different_source():
    assert deterministic_document_id("a.md") != deterministic_document_id("b.md")


def test_deterministic_point_id_stable_for_same_document_and_chunk_index():
    doc_id = deterministic_document_id("guia/erro-idoc.md")
    assert deterministic_point_id(doc_id, 0) == deterministic_point_id(doc_id, 0)


def test_deterministic_point_id_differs_by_chunk_index():
    doc_id = deterministic_document_id("guia/erro-idoc.md")
    assert deterministic_point_id(doc_id, 0) != deterministic_point_id(doc_id, 1)


class _FakeEmbeddings:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # vetor fake, so precisa ter tamanho fixo - o conteudo nao importa
        # para estes testes (nao verificamos o valor do vetor, so os ids).
        return [[0.1, 0.2, 0.3] for _ in texts]


class _FakeQdrantClient:
    def __init__(self):
        self.upsert_calls: list[dict] = []

    def upsert(self, collection_name: str, points: list) -> None:
        self.upsert_calls.append({"collection_name": collection_name, "points": points})


def test_embed_and_upsert_reuses_same_point_ids_when_source_content_changes():
    """Simula reingestao do MESMO arquivo (source) apos uma edicao de
    conteudo: sem delete previo, o segundo upsert deve sobrescrever os
    mesmos point ids do primeiro (para os indices de chunk que
    continuam existindo) - nao criar pontos novos/orfaos."""
    source = "guia/erro-idoc.md"
    client = _FakeQdrantClient()
    embeddings = _FakeEmbeddings()

    document_id_v1 = deterministic_document_id(source)
    embed_and_upsert(
        client,
        "sap_incident_docs",
        embeddings,
        [{"text": "conteudo original", "page_number": None}],
        source,
        hybrid=False,
        doc_meta={"document_id": document_id_v1, "filename": "erro-idoc.md"},
    )

    # Conteudo do arquivo "mudou" - mas o source (caminho) e o mesmo, entao
    # deterministic_document_id() continua igual (nao depende mais do hash).
    document_id_v2 = deterministic_document_id(source)
    embed_and_upsert(
        client,
        "sap_incident_docs",
        embeddings,
        [{"text": "conteudo editado, mais longo que o original", "page_number": None}],
        source,
        hybrid=False,
        doc_meta={"document_id": document_id_v2, "filename": "erro-idoc.md"},
    )

    assert document_id_v1 == document_id_v2
    assert len(client.upsert_calls) == 2
    first_point_id = client.upsert_calls[0]["points"][0].id
    second_point_id = client.upsert_calls[1]["points"][0].id
    assert first_point_id == second_point_id


# ---------------------------------------------------------------------------
# Testes de exit code / contagem de erros
# ---------------------------------------------------------------------------


def test_run_ingest_returns_zero_when_nothing_pending(tmp_path, monkeypatch):
    """run_ingest retorna 0 (sem erros) quando nao ha arquivos pendentes."""
    import app.rag.ingest as ingest_module

    monkeypatch.setattr(ingest_module, "QDRANT_URL", "http://localhost:6333")
    monkeypatch.setattr(ingest_module, "find_files", lambda src, excl: [])
    monkeypatch.setattr(ingest_module, "load_state", lambda _: {})
    monkeypatch.setattr(ingest_module, "resolve_source_dir", lambda cfg: tmp_path)

    result = ingest_module.run_ingest(
        "incidents", limit=None, excludes=[], reset_state=False, reset_collection=False
    )
    assert result == 0


def test_run_ingest_returns_error_count_on_failure(tmp_path, monkeypatch):
    """run_ingest retorna o numero de arquivos que lancaram excecao dentro de process_one."""
    import app.rag.ingest as ingest_module

    bad_file = tmp_path / "bad.md"
    bad_file.write_text("conteudo que vai falhar no embed")

    monkeypatch.setattr(ingest_module, "QDRANT_URL", "http://localhost:6333")
    monkeypatch.setattr(ingest_module, "find_files", lambda src, excl: [bad_file])
    monkeypatch.setattr(ingest_module, "load_state", lambda _: {})
    monkeypatch.setattr(ingest_module, "save_state", lambda *_: None)
    monkeypatch.setattr(ingest_module, "resolve_source_dir", lambda cfg: tmp_path)

    # QdrantClient precisa existir (criado antes de pending check); faz um stub minimo
    class _FakeQdrant:
        def get_collections(self):
            class _R:
                collections = []

            return _R()

        def get_collection(self, *a, **kw):
            raise Exception("nao existe")

        def create_collection(self, *a, **kw):
            pass

    monkeypatch.setattr(ingest_module, "QdrantClient", lambda **kw: _FakeQdrant())

    # embed_and_upsert e o ponto onde o erro de verdade ocorreria
    def _fail_embed(*args, **kwargs):
        raise RuntimeError("embedding falhou")

    monkeypatch.setattr(ingest_module, "embed_and_upsert", _fail_embed)

    # OllamaEmbeddings e probe_vector_size precisam de stubs
    class _FakeEmbeddings:
        pass

    monkeypatch.setattr(ingest_module, "OllamaEmbeddings", lambda model: _FakeEmbeddings())
    monkeypatch.setattr(ingest_module, "probe_vector_size", lambda emb: 768)
    monkeypatch.setattr(ingest_module, "ensure_collection", lambda *a, **kw: None)

    result = ingest_module.run_ingest(
        "incidents", limit=None, excludes=[], reset_state=False, reset_collection=False
    )
    assert result == 1
