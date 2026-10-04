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

import pytest

from app.rag.ingest import (
    deterministic_document_id,
    deterministic_point_id,
    embed_and_upsert,
    infer_category,
)


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
    # DA-45: stamp_existing_collection e' chamada no no-op para carimbar a
    # identidade (se a collection existir), mas e' ruido para este teste
    # (queremos medir so o return code). Stub para no-op tambem.
    monkeypatch.setattr(ingest_module, "stamp_existing_collection", lambda *a, **kw: None)

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
                collections = []  # noqa: RUF012

            return _R()

        def get_collection(self, *a, **kw):
            raise RuntimeError("nao existe")

        def create_collection(self, *a, **kw):
            pass

        # DA-45: `stamp_collection` roda a CADA ingest (nao so na criacao
        # da collection) para gravar a identidade do embedding. Este teste
        # isola `process_one`, entao o stamp e' ruido - e' stubado aqui
        # para o teste continuar medindo so o que ele diz medir.

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
    monkeypatch.setattr(ingest_module, "stamp_collection", lambda *a, **kw: None)

    result = ingest_module.run_ingest(
        "incidents", limit=None, excludes=[], reset_state=False, reset_collection=False
    )
    assert result == 1


# --- DA-45: stamp_existing_collection no no-op ------------------------------
# Testes que o early-exit 'nada a fazer' chama stamp_existing_collection e
# o guarda funciona corretamente (protege contra divergencia comprovada).


class _FakeEmbeddingsWithSize:
    def __init__(self, size: int):
        self._size = size

    def embed_query(self, text: str) -> list[float]:
        return [0.0] * self._size


class _FakeIdentityCollection:
    def __init__(self, fingerprint: str | None = None):
        self.fingerprint = fingerprint

    def read_fingerprint(self, client, collection_name: str) -> str | None:
        return self.fingerprint


def test_run_ingest_noop_stamps_existing_collection(tmp_path, monkeypatch):
    """DA-45: no-op run (pending==0) chama stamp_existing_collection na
    collection existente, que carimba a identidade (se ausente ou igual)."""
    import app.rag.ingest as ingest_module

    # Fake que simula collection existente (fingerprint ausente = pre-DA-45)
    monkeypatch.setattr(ingest_module, "QDRANT_URL", "http://localhost:6333")
    monkeypatch.setattr(ingest_module, "find_files", lambda src, excl: [])
    monkeypatch.setattr(ingest_module, "load_state", lambda _: {})
    monkeypatch.setattr(ingest_module, "resolve_source_dir", lambda cfg: tmp_path)

    # Fake Qdrant: collection existe
    class _FakeQdrant:
        def get_collections(self):
            class _R:
                collections = [type("C", (), {"name": "iic_incidents"})()]  # noqa: RUF012

            return _R()

        def get_collection(self, *a, **kw):
            # Simula collection real com schema OK
            class _Config:
                class _Params:
                    vectors = {"dense": type("V", (), {"size": 768})()}  # noqa: RUF012
                    sparse_vectors = None

                params = _Params()

            return type("Info", (), {"config": _Config})()

    monkeypatch.setattr(ingest_module, "QdrantClient", lambda **kw: _FakeQdrant())

    # Fake embeddings (probe vai usar este size)
    monkeypatch.setattr(ingest_module, "get_query_embeddings", lambda: _FakeEmbeddingsWithSize(768))
    monkeypatch.setattr(ingest_module, "probe_vector_size", lambda emb: 768)

    # Mock da collection lateral ( identity ) para retorno ausente
    monkeypatch.setattr(
        ingest_module, "verify_collection_embedding", lambda *a, **kw: "[AVISO] identidade ausente"
    )

    # Conta chamadas de stamp_existing_collection (não stamp_collection)
    stamp_existing_calls = []
    monkeypatch.setattr(
        ingest_module,
        "stamp_existing_collection",
        lambda *a, **kw: stamp_existing_calls.append((a, kw)),
    )

    result = ingest_module.run_ingest(
        "incidents", limit=None, excludes=[], reset_state=False, reset_collection=False
    )

    assert result == 0
    assert len(stamp_existing_calls) == 1
    assert stamp_existing_calls[0][0][1] == "sap_incident_docs"  # collection_name


def test_run_ingest_noop_raises_on_proven_divergence(tmp_path, monkeypatch):
    """DA-45: no-op run levanta EmbeddingMismatchError quando ha divergencia
    comprovada (fingerprint diferente) ANTES de carimbar."""
    import app.rag.ingest as ingest_module
    from app.rag.embedding_guard import EmbeddingMismatchError

    monkeypatch.setattr(ingest_module, "QDRANT_URL", "http://localhost:6333")
    monkeypatch.setattr(ingest_module, "find_files", lambda src, excl: [])
    monkeypatch.setattr(ingest_module, "load_state", lambda _: {})
    monkeypatch.setattr(ingest_module, "resolve_source_dir", lambda cfg: tmp_path)

    class _FakeQdrant:
        def get_collections(self):
            class _R:
                collections = [type("C", (), {"name": "iic_incidents"})()]  # noqa: RUF012

            return _R()

        def get_collection(self, *a, **kw):
            class _Config:
                class _Params:
                    vectors = {"dense": type("V", (), {"size": 768})()}  # noqa: RUF012
                    sparse_vectors = None

                params = _Params()

            return type("Info", (), {"config": _Config})()

    monkeypatch.setattr(ingest_module, "QdrantClient", lambda **kw: _FakeQdrant())
    monkeypatch.setattr(ingest_module, "get_query_embeddings", lambda: _FakeEmbeddingsWithSize(768))
    monkeypatch.setattr(ingest_module, "probe_vector_size", lambda emb: 768)

    # stamp_existing_collection levanta EmbeddingMismatchError simulando divergencia
    def _raise_divergence(*a, **kw):
        raise EmbeddingMismatchError("fingerprint divergente")

    monkeypatch.setattr(ingest_module, "stamp_existing_collection", _raise_divergence)

    with pytest.raises(EmbeddingMismatchError):
        ingest_module.run_ingest(
            "incidents", limit=None, excludes=[], reset_state=False, reset_collection=False
        )


def test_run_ingest_noop_does_not_create_missing_collection(tmp_path, monkeypatch):
    """DA-45: no-op run com collection ausente NAO a cria (nem carimba)."""
    import app.rag.ingest as ingest_module

    monkeypatch.setattr(ingest_module, "QDRANT_URL", "http://localhost:6333")
    monkeypatch.setattr(ingest_module, "find_files", lambda src, excl: [])
    monkeypatch.setattr(ingest_module, "load_state", lambda _: {})
    monkeypatch.setattr(ingest_module, "resolve_source_dir", lambda cfg: tmp_path)

    # Fake Qdrant: NENHUMA collection existe
    class _FakeQdrant:
        def get_collections(self):
            class _R:
                collections = []  # noqa: RUF012

            return _R()

    monkeypatch.setattr(ingest_module, "QdrantClient", lambda **kw: _FakeQdrant())
    monkeypatch.setattr(ingest_module, "get_query_embeddings", lambda: _FakeEmbeddingsWithSize(768))
    monkeypatch.setattr(ingest_module, "probe_vector_size", lambda emb: 768)
    monkeypatch.setattr(ingest_module, "verify_collection_embedding", lambda *a, **kw: None)

    # Conta chamadas de stamp_collection
    stamp_calls = []
    monkeypatch.setattr(
        ingest_module, "stamp_collection", lambda *a, **kw: stamp_calls.append((a, kw))
    )

    result = ingest_module.run_ingest(
        "incidents", limit=None, excludes=[], reset_state=False, reset_collection=False
    )

    assert result == 0
    assert len(stamp_calls) == 0  # NAO chamado: collection ausente


# --- infer_category: casamento por TOKEN, nunca por substring -------------
# Regressao real: "order" e substring de "ordering", entao
# po_pi_message_ordering.md era indexado como categoria "sales". O mesmo
# defeito classificaria "capital" como "integration" ("api"), "throttle"
# como "hcm" ("hr") e "profile" como "finance" ("fi").


def test_infer_category_casado_por_token_nao_por_substring():
    assert infer_category("po_pi_message_ordering.md") == "general"
    assert infer_category("x_capital_review.md") == "general"
    assert infer_category("x_profile_change.md") == "general"
    assert infer_category("x_connector_error.md") == "general"
    assert infer_category("x_wrapped_request.md") == "general"
    assert infer_category("x_author_lookup.md") == "general"
    assert infer_category("x_comm_summary.md") == "general"
    assert infer_category("x_rapid_deploy.md") == "general"
    assert infer_category("x_sdn_switch.md") == "general"


def test_infer_category_preserva_buckets_por_token_exato():
    assert infer_category("cpi_http_401.md") == "integration"
    assert infer_category("idoc_status_51.md") == "integration"
    assert infer_category("rfc_connection_refused.md") == "abap"
    assert infer_category("ariba_po_supplier_mismatch.md") == "procurement"
    assert infer_category("cap_custom_purchase_approval_failure.md") == "cap_btp"
    assert infer_category("apim_gateway_auth_throttle.md") == "security"


def test_infer_category_salesforce_continua_sales():
    # "sales" e substring de "salesforce": antes dependia desse acidente.
    assert infer_category("salesforce_case_sap_sync_failure.md") == "sales"


def test_infer_category_aceita_plural_e_camel_case():
    assert infer_category("workday_successfactors_sync_error.md") == "hcm"
    assert infer_category("abapOrdersTimeout.md") == "abap"
    assert infer_category("sem_extensao") == "general"
    assert infer_category("") == "general"
