"""DA-45: identidade do embedding.

O teste central deste arquivo e'
`test_dimensao_igual_nao_significa_embedding_igual`: ele reproduz o
caso que a checagem de dimensao existente NAO pega - dois modelos
diferentes com os mesmos 768 dimensoes. E' o cenario mais provavel
(num_model * 768 = num_model) e o mais perigoso, porque a busca
continua respondendo, so que com resposta inventada.
"""

from __future__ import annotations

import pytest

from app.rag.embedding_guard import (
    FINGERPRINT_KEY,
    EmbeddingMismatchError,
    describe_dense_size,
    embedding_fingerprint,
    read_fingerprint,
    reset_verification_cache,
    stamp_collection,
    verify_collection_embedding,
    verify_once,
)


class FakeInfo:
    """Dublê de `client.get_collection()` - só o que o guard lê."""

    def __init__(self, size: int | None = 768, metadata: dict | None = None):
        dense = type("V", (), {"size": size})()
        params = type("P", (), {"vectors": {"dense": dense}, "sparse_vectors": {}})()
        self.config = type("C", (), {"params": params})()
        self.metadata = metadata


# --------------------------------------------------------------------------
# Fingerprint
# --------------------------------------------------------------------------


def test_fingerprint_e_estavel():
    """Dois processos com o mesmo .env precisam produzir o mesmo valor -
    e' isso que torna a verificacao util entre execucoes."""
    a = embedding_fingerprint("nomic-embed-text", "ollama")
    b = embedding_fingerprint("nomic-embed-text", "ollama")
    assert a == b


def test_fingerprint_muda_com_o_modelo():
    assert embedding_fingerprint("nomic-embed-text") != embedding_fingerprint("mxbai-embed-large")


def test_fingerprint_muda_com_o_provider():
    """Mesmo modelo, provider diferente = origem do embedding diferente,
    e nao se pode assumir que produz o mesmo espaco vetorial."""
    assert embedding_fingerprint("all-minilm", "ollama") != embedding_fingerprint(
        "all-minilm", "openai"
    )


def test_fingerprint_e_opaco():
    """Gravado em metadata que aparece em GET /collections e em log de
    suporte: nao deve publicar o nome do modelo."""
    fp = embedding_fingerprint("nomic-embed-text", "ollama")
    assert "nomic" not in fp
    assert len(fp) == 12


def test_fingerprint_distingue_mesma_dimensao():
    """768 e' a dimensao mais comum do ecossistema; a identidade e' o que
    separa."""
    assert embedding_fingerprint("nomic-embed-text") != embedding_fingerprint("mxbai-embed-large")


# --------------------------------------------------------------------------
# Leitura de metadado
# --------------------------------------------------------------------------


def test_le_fingerprint_gravado():
    info = FakeInfo(metadata={FINGERPRINT_KEY: "fingerprint-de-teste"})
    assert read_fingerprint(info) == "fingerprint-de-teste"


@pytest.mark.parametrize("metadata", [None, {}, {FINGERPRINT_KEY: ""}, "nao-dict"])
def test_fingerprint_ausente_vira_none(metadata):
    assert read_fingerprint(FakeInfo(metadata=metadata)) is None


def test_le_dimensao_dos_dois_formatos_de_schema():
    """O Qdrant aceita vetor sem nome ou nomeado; o guard tem de ler os
    dois, como ensure_collection."""
    nomeado = FakeInfo(size=768)
    assert describe_dense_size(nomeado) == 768

    denso = type("V", (), {"size": 384})()
    params = type("P", (), {"vectors": denso})()
    info = type("I", (), {"config": type("C", (), {"params": params})(), "metadata": None})()
    assert describe_dense_size(info) == 384


# --------------------------------------------------------------------------
# Verificacao: os tres estados
# --------------------------------------------------------------------------


def test_fingerprint_bate_nao_avisa_e_nao_erra():
    model = "nomic-embed-text"
    info = FakeInfo(metadata={FINGERPRINT_KEY: embedding_fingerprint(model, "ollama")})
    assert verify_collection_embedding(info, "col", expected_model=model, expected_size=768) is None


def test_fingerprint_ausente_avisa_sem_errar():
    """Estado honesto: nao da para saber, entao DIZ. Tratar como 'ok' seria
    o proprio bug do modulo; errar derrubaria um corpus de 22 GB por causa
    de metadado ausente."""
    aviso = verify_collection_embedding(
        FakeInfo(metadata=None), "col", expected_model="nomic-embed-text", expected_size=768
    )
    assert aviso is not None
    assert "nao tem identidade de embedding" in aviso
    assert "768" in aviso, "o aviso deve expor a dimensao que coincide - e' o que confunde"


def test_dimensao_igual_nao_significa_embedding_igual():
    """O caso que a checagem de dimensao NAO pega: mesma dimensao,
    identidade diferente. Este teste e' a razao de o modulo existir."""
    info = FakeInfo(
        size=768,  # mesma dimensao - a checagem antiga passaria
        metadata={FINGERPRINT_KEY: embedding_fingerprint("mxbai-embed-large", "ollama")},
    )
    with pytest.raises(EmbeddingMismatchError) as exc:
        verify_collection_embedding(
            info, "col", expected_model="nomic-embed-text", expected_size=768
        )
    assert "nao sao comparaveis" in str(exc.value)


def test_dimensao_diferente_erra_mesmo_sem_fingerprint():
    """Divergencia de dimensao e' prova sozinha, independente do metadado."""
    info = FakeInfo(size=384, metadata=None)
    with pytest.raises(EmbeddingMismatchError) as exc:
        verify_collection_embedding(
            info, "col", expected_model="nomic-embed-text", expected_size=768
        )
    assert "384" in str(exc.value) and "768" in str(exc.value)


def test_erro_de_divergencia_diz_como_resolver():
    """Mensagem de erro que nao diz o comando e' meio erro."""
    info = FakeInfo(size=384, metadata=None)
    with pytest.raises(EmbeddingMismatchError) as exc:
        verify_collection_embedding(
            info, "col", expected_model="nomic-embed-text", expected_size=768
        )
    assert "--reset" in str(exc.value)


# --------------------------------------------------------------------------
# verify_once: uma ida ao Qdrant por processo, nao por query
# --------------------------------------------------------------------------


class FakeClient:
    def __init__(self, info: FakeInfo):
        self.info = info
        self.calls = 0
        self.stamps: list[tuple[str, str]] = []
        self.updated: dict[str, dict] = {}

    def get_collection(self, _name: str) -> FakeInfo:
        self.calls += 1
        return self.info

    def update_collection(self, collection_name: str, metadata: dict) -> None:
        self.updated[collection_name] = metadata
        self.stamps.append((collection_name, metadata[FINGERPRINT_KEY]))


def test_verify_once_nao_repete_a_ida_ao_qdrant():
    """O guard nao pode virar latencia por query no caminho quente."""
    reset_verification_cache()
    model = "nomic-embed-text"
    client = FakeClient(
        FakeInfo(metadata={FINGERPRINT_KEY: embedding_fingerprint(model, "ollama")})
    )
    for _ in range(25):
        verify_once(client, "col", model)
    assert client.calls == 1


def test_verify_once_cobre_cada_collection():
    reset_verification_cache()
    client = FakeClient(FakeInfo(metadata={FINGERPRINT_KEY: embedding_fingerprint("m", "ollama")}))
    verify_once(client, "sap_incident_docs", "m")
    verify_once(client, "sap_reference_library", "m")
    assert client.calls == 2


def test_stamp_grava_no_metadata_da_collection():
    """`update_collection`, nao `set_payload`: payload e' de ponto, e a
    identidade do embedding e' da collection inteira."""
    reset_verification_cache()
    client = FakeClient(FakeInfo())
    stamp_collection(client, "sap_incident_docs", "nomic-embed-text")
    assert client.updated["sap_incident_docs"][FINGERPRINT_KEY] == embedding_fingerprint(
        "nomic-embed-text", "ollama"
    )


def test_apos_stamp_a_verificacao_passa(caplog):
    """O ciclo completo que resolve o estado 'desconhecido': uma collection
    legada recebe identidade no proximo ingest, e passa a ser verificavel
    definitivamente. Este e' o caminho que evita exigir --reset (22 GB)
    so para poder ganhar um metadado."""
    reset_verification_cache()
    model = "nomic-embed-text"
    client = FakeClient(FakeInfo(metadata=None))

    with caplog.at_level("WARNING", logger="app.rag.embedding_guard"):
        verify_once(client, "col", model)
    assert any("nao tem identidade de embedding" in r.message for r in caplog.records)

    # proximo ingest grava a identidade...
    stamp_collection(client, "col", model)
    client.info = FakeInfo(metadata=client.updated["col"])

    # ...e a partir dai a verificacao fica definitiva e silenciosa.
    caplog.clear()
    reset_verification_cache()
    with caplog.at_level("WARNING", logger="app.rag.embedding_guard"):
        verify_once(client, "col", model)
    assert not [r for r in caplog.records if "nao tem identidade" in r.message]
