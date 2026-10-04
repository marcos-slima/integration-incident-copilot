"""DA-45: identidade do embedding.

O teste central deste arquivo e'
`test_dimensao_igual_nao_significa_embedding_igual`: ele reproduz o
caso que a checagem de dimensao existente NAO pega - dois modelos
diferentes com os mesmos 768 dimensoes. E' o cenario mais provavel
(num_model * 768 = num_model) e o mais perigoso, porque a busca
continua respondendo, so que com resposta inventada.

O duble aqui `FakeClient` expoe SO a API que o qdrant-client 1.19
realmente tem. Isso e' deliberado: a versao anterior deste arquivo
usava um `FakeInfo` com atributo `metadata`, que o cliente real NAO
tem - e o guard dependia desse atributo inexistente. O teste passava
enquanto `stamp_collection` era um no-op silencioso em producao. Um
dule que concorda com o cliente real e' a unica forma de o teste
perceber quando o guard usa uma API que nao existe.
"""

from __future__ import annotations

import httpx
import pytest
from qdrant_client.http import models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.rag.embedding_guard import (
    FINGERPRINT_KEY,
    IDENTITY_COLLECTION,
    IDENTITY_VECTOR_SIZE,
    EmbeddingMismatchError,
    describe_dense_size,
    embedding_fingerprint,
    identity_point_id,
    read_fingerprint,
    reset_verification_cache,
    stamp_collection,
    verify_collection_embedding,
    verify_once,
)


def _qdrant_error(status_code: int, message: str) -> UnexpectedResponse:
    """Erro do Qdrant com o tipo e a assinatura reais do cliente 1.19.

    `UnexpectedResponse` nao recebe um `httpx.Response`: recebe
    (status_code, reason_phrase, content, headers) e monta o response
    ele mesmo. Confirmei a assinatura em 1.19.0 em vez de supor - o
    guard depende de distinguir esta excecao de qualquer outra.
    """
    return UnexpectedResponse(
        status_code=status_code,
        reason_phrase="Bad Request" if status_code == 400 else "Not Found",
        content=message.encode(),
        headers=httpx.Headers({"content-type": "application/json"}),
    )


class FakeInfo:
    """Duble de `client.get_collection()` - so o que o guard le.

    Deliberadamente SEM atributo `metadata`: e' assim que o
    `CollectionInfo` do qdrant-client 1.19 se comporta, e um duble que
    tivesse o atributo esconderia o bug que este arquivo agora cobre.
    """

    def __init__(self, size: int | None = 768):
        dense = type("V", (), {"size": size})()
        params = type("P", (), {"vectors": {"dense": dense}, "sparse_vectors": {}})()
        self.config = type("C", (), {"params": params})()


class FakeClient:
    """Duble de `QdrantClient`, limitado ao que o guard usa.

    Modela a collection lateral de identidade como um dicionario de
    pontos, porque e' isso que ela e'. `metadata` nao existe aqui: quem
    tentasse gravar identidade nela receberia um AttributeError, que e'
    exatamente o que o cliente real faria.
    """

    def __init__(self, info: FakeInfo | None = None, size: int | None = 768):
        self.info = info if info is not None else FakeInfo(size=size)
        self.info_by_name = {
            c: self.info for c in ("col", "sap_incident_docs", "sap_reference_library")
        }
        self.points: dict[str, object] = {}
        self.collections: set[str] = set(self.info_by_name)
        self.get_calls = 0
        self.created: list[str] = []
        self.deleted: list[str] = []
        self.upserts: list[tuple[str, object]] = []

    # --- API de collection -------------------------------------------------

    def get_collection(self, name: str) -> FakeInfo:
        self.get_calls += 1
        if name not in self.collections:
            # Tipo e status observados no qdrant-client 1.19 real: `get_
            # collection` devolve 404 e `retrieve` numa collection
            # inexistente devolve 400, ambos `UnexpectedResponse`. O duble
            # usa os tipos reais porque o guard distingue "collection
            # ausente" (UnexpectedResponse) de "infra no ar" (qualquer
            # outra excecao) - um duble com RuntimeError faria o teste
            # passar com um `except Exception` que em producao mascararia
            # Qdrant fora do ar como "sem fingerprint".
            raise _qdrant_error(404, f"Collection {name} not found")
        # Sem entrada propria, cai no `self.info` padrao - que e' o que o
        # cliente real faz: qualquer collection de conteudo tem o mesmo
        # schema. A collection lateral tem o dela, registrada explicitamente.
        return self.info_by_name.get(name, self.info)

    def delete_collection(self, name: str) -> None:
        self.deleted.append(name)
        self.collections.discard(name)
        self.info_by_name.pop(name, None)
        self.points.clear()

    def create_collection(self, collection_name: str, vectors_config=None) -> None:
        if collection_name in self.collections:
            raise _qdrant_error(400, f"Wrong input: Collection {collection_name} already exists!")
        self.collections.add(collection_name)
        self.created.append(collection_name)
        if collection_name == IDENTITY_COLLECTION:
            self.info_by_name[IDENTITY_COLLECTION] = FakeInfo(size=IDENTITY_VECTOR_SIZE)

    # --- API de ponto ------------------------------------------------------

    def retrieve(self, collection_name: str, ids: list, with_payload: bool, with_vectors: bool):
        if collection_name not in self.collections:
            raise _qdrant_error(400, f"Collection {collection_name} not found")
        return [_Point(self.points[i].id, self.points[i].payload) for i in ids if i in self.points]

    def upsert(self, collection_name: str, points: list) -> None:
        self.upserts.append((collection_name, points[0]))
        for point in points:
            self.points[point.id] = point


class _Point:
    def __init__(self, point_id: str, payload: dict):
        self.id = point_id
        self.payload = payload


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
    """Gravado onde aparece em GET /collections e em log de suporte:
    nao deve publicar o nome do modelo."""
    fp = embedding_fingerprint("nomic-embed-text", "ollama")
    assert "nomic" not in fp
    assert len(fp) == 12


def test_fingerprint_distingue_mesma_dimensao():
    """768 e' a dimensao mais comum do ecossistema; a identidade e' o que
    separa."""
    assert embedding_fingerprint("nomic-embed-text") != embedding_fingerprint("mxbai-embed-large")


def test_identity_point_id_e_estavel_e_distinto_por_collection():
    """Id deterministico: `stamp_collection` roda em todo ingest e nao pode
    duplicar registro. E por collection: duas collections com o mesmo
    embedding sao entradas DIFERENTES da tabela de identidade."""
    assert identity_point_id("col") == identity_point_id("col")
    assert identity_point_id("col") != identity_point_id("sap_incident_docs")


# --------------------------------------------------------------------------
# Leitura: a identidade vem da collection lateral
# --------------------------------------------------------------------------


def test_le_fingerprint_gravado():
    client = FakeClient()
    client.collections.add(IDENTITY_COLLECTION)
    client.points[identity_point_id("col")] = models.PointStruct(
        id=identity_point_id("col"), vector=[0.0], payload={FINGERPRINT_KEY: "fingerprint-de-teste"}
    )
    assert read_fingerprint(client, "col") == "fingerprint-de-teste"


def test_fingerprint_ausente_vira_none():
    """Collection lateral criada mas sem o ponto desta collection."""
    client = FakeClient()
    client.collections.add(IDENTITY_COLLECTION)
    assert read_fingerprint(client, "col") is None


def test_collection_lateral_ausente_vira_none_e_nao_levanta():
    """Projeto pre-DA-45: a collection lateral nao existe. 'Nao sei' e' o
    estado honesto, e NAO pode virar excecao - a retrieval nao pode cair
    por causa de metadado faltando."""
    client = FakeClient()
    assert IDENTITY_COLLECTION not in client.collections
    assert read_fingerprint(client, "col") is None


@pytest.mark.parametrize("payload", [{}, {FINGERPRINT_KEY: ""}, {FINGERPRINT_KEY: None}])
def test_fingerprint_invalido_vira_none(payload):
    client = FakeClient()
    client.collections.add(IDENTITY_COLLECTION)
    pid = identity_point_id("col")
    client.points[pid] = models.PointStruct(id=pid, vector=[0.0], payload=payload)
    assert read_fingerprint(client, "col") is None


def test_le_dimensao_dos_dois_formatos_de_schema():
    """O Qdrant aceita vetor sem nome ou nomeado; o guard tem de ler os
    dois, como ensure_collection."""
    client = FakeClient(FakeInfo(size=768))
    assert describe_dense_size(client, "col") == 768

    denso = type("V", (), {"size": 384})()
    params = type("P", (), {"vectors": denso})()
    info = type("I", (), {"config": type("C", (), {"params": params})()})()
    client = FakeClient(FakeInfo())
    client.info_by_name["col"] = info
    assert describe_dense_size(client, "col") == 384


def test_dimensao_de_collection_inexistente_vira_none():
    """Ausencia de collection e' ausencia de informacao - e' distinto de
    'existe com dimensao 0', que seria schema invalido."""
    client = FakeClient()
    assert describe_dense_size(client, "nao-existe") is None


# --------------------------------------------------------------------------
# Verificacao: os tres estados
# --------------------------------------------------------------------------


def test_fingerprint_bate_nao_avisa_e_nao_erra():
    model = "nomic-embed-text"
    client = FakeClient()
    stamp_collection(client, "col", model)
    assert (
        verify_collection_embedding(client, "col", expected_model=model, expected_size=768) is None
    )


def test_fingerprint_ausente_avisa_sem_errar():
    """Estado honesto: nao da para saber, entao DIZ. Tratar como 'ok' seria
    o proprio bug do modulo; errar derrubaria um corpus de 22 GB por causa
    de metadado ausente."""
    client = FakeClient()
    aviso = verify_collection_embedding(
        client, "col", expected_model="nomic-embed-text", expected_size=768
    )
    assert aviso is not None
    assert "nao tem identidade de embedding" in aviso
    assert "768" in aviso, "o aviso deve expor a dimensao que coincide - e' o que confunde"


def test_dimensao_igual_nao_significa_embedding_igual():
    """O caso que a checagem de dimensao NAO pega: mesma dimensao,
    identidade diferente. Este teste e' a razao de o modulo existir."""
    client = FakeClient(FakeInfo(size=768))  # mesma dimensao - a checagem antiga passaria
    stamp_collection(client, "col", "mxbai-embed-large")
    with pytest.raises(EmbeddingMismatchError) as exc:
        verify_collection_embedding(
            client, "col", expected_model="nomic-embed-text", expected_size=768
        )
    assert "nao sao comparaveis" in str(exc.value)


def test_dimensao_diferente_erra_mesmo_sem_fingerprint():
    """Divergencia de dimensao e' prova sozinha, independente do metadado."""
    client = FakeClient(FakeInfo(size=384))
    with pytest.raises(EmbeddingMismatchError) as exc:
        verify_collection_embedding(
            client, "col", expected_model="nomic-embed-text", expected_size=768
        )
    assert "384" in str(exc.value) and "768" in str(exc.value)


def test_erro_de_divergencia_diz_como_resolver():
    """Mensagem de erro que nao diz o comando e' meio erro."""
    client = FakeClient(FakeInfo(size=384))
    with pytest.raises(EmbeddingMismatchError) as exc:
        verify_collection_embedding(
            client, "col", expected_model="nomic-embed-text", expected_size=768
        )
    assert "--reset" in str(exc.value)


# --------------------------------------------------------------------------
# verify_once: uma ida ao Qdrant por processo, nao por query
# --------------------------------------------------------------------------


def test_verify_once_nao_repete_a_ida_ao_qdrant():
    """O guard nao pode virar latencia por query no caminho quente."""
    reset_verification_cache()
    model = "nomic-embed-text"
    client = FakeClient()
    stamp_collection(client, "col", model)
    client.get_calls = 0
    for _ in range(25):
        verify_once(client, "col", model)
    assert client.get_calls == 0, "com fingerprint gravado, nem chega a ler a collection"


def test_verify_once_cobre_cada_collection():
    reset_verification_cache()
    client = FakeClient()
    stamp_collection(client, "sap_incident_docs", "m")
    stamp_collection(client, "sap_reference_library", "m")
    client.get_calls = 0
    verify_once(client, "sap_incident_docs", "m")
    verify_once(client, "sap_reference_library", "m")
    assert client.get_calls == 0


# --------------------------------------------------------------------------
# Gravacao: a identidade precisa ser RELIVEL
# --------------------------------------------------------------------------


def test_stamp_grava_ponto_na_collection_lateral():
    """A identidade e' da collection INTEIRA, mas o Qdrant 1.19 nao persiste
    `collection.metadata` - a via antiga era no-op silencioso. A gravacao
    que funciona e' um ponto, e por isso vai para uma collection lateral
    em vez de `update_collection`."""
    reset_verification_cache()
    client = FakeClient(FakeInfo())
    stamp_collection(client, "sap_incident_docs", "nomic-embed-text")

    collection_name, point = client.upserts[-1]
    assert collection_name == IDENTITY_COLLECTION
    assert point.payload[FINGERPRINT_KEY] == embedding_fingerprint("nomic-embed-text", "ollama")
    assert point.payload["collection"] == "sap_incident_docs"
    assert point.payload["dense_size"] == 768


def test_stamp_usa_vetor_placeholder_de_1_dim():
    """O Qdrant exige o campo `vector` em todo ponto - um ponto sem ele e'
    rejeitado com `missing field vector`, tanto no cliente quanto no
    servidor. O registro nao e' um vetor de conteudo, mas precisa ter um."""
    client = FakeClient(FakeInfo())
    stamp_collection(client, "col", "m")
    point = client.upserts[-1][1]
    assert point.vector == [0.0]


def test_stamp_e_idempotente():
    """`stamp_collection` roda a cada ingest; rodar duas vezes nao pode
    duplicar registro nem mudar o id."""
    client = FakeClient(FakeInfo())
    stamp_collection(client, "col", "m")
    stamp_collection(client, "col", "m")
    ids = [pid for pid in client.points]
    assert len(ids) == 1
    assert ids[0] == identity_point_id("col")


def test_stamp_cria_a_collection_lateral_uma_vez():
    client = FakeClient(FakeInfo())
    stamp_collection(client, "col", "m")
    stamp_collection(client, "outra", "m")
    assert client.created == [IDENTITY_COLLECTION]


def test_stamp_repara_collection_lateral_vector_less():
    """Uma versao anterior criava a collection lateral sem `vectors_config`,
    e o Qdrant nao adiciona vetor por `PATCH` ("Not existing vector name").
    Sem reparo, `stamp_collection` falharia com 400 para sempre - e o
    ingest inteiro junto, porque ele chama o stamp a cada run."""
    client = FakeClient(FakeInfo())
    client.collections.add(IDENTITY_COLLECTION)  # ja existe, vector-less
    client.info_by_name[IDENTITY_COLLECTION] = FakeInfo(size=None)

    stamp_collection(client, "col", "nomic-embed-text")

    assert client.deleted == [IDENTITY_COLLECTION]
    assert read_fingerprint(client, "col") == embedding_fingerprint("nomic-embed-text", "ollama")


def test_stamp_nao_recria_collection_lateral_saudavel():
    """O reparo so pode disparar no schema errado: recriar a cada run
    apagaria a identidade recem-gravada e o guard nunca sairia do estado
    desconhecido."""
    client = FakeClient(FakeInfo())
    stamp_collection(client, "col", "m")
    client.deleted.clear()
    client.collections.add(IDENTITY_COLLECTION)
    client.info_by_name[IDENTITY_COLLECTION] = FakeInfo(size=IDENTITY_VECTOR_SIZE)

    stamp_collection(client, "col", "m")
    assert client.deleted == []
    assert read_fingerprint(client, "col") is not None


def test_infra_fora_do_ar_nao_vira_sem_fingerprint():
    """Qdrant inalcancavel tem de aparecer como falha, nao como 'sem
    identidade'.

    `read_fingerprint` devolve None para collection ausente, e o chamador
    traduz None em AVISO de identidade desconhecida - que o ingest le e
    segue adiante. Se uma conexao recusada produzisse o mesmo None, um
    Qdrant fora do ar pareceria 'collection legada ainda sem registro' e
    o aviso ensinaria a pessoa a rodar `--reset` de 22 GB contra um
    problema de rede. So `UnexpectedResponse` e' 'nao sei'.
    """
    client = FakeClient()

    def _offline(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    client.retrieve = _offline  # type: ignore[method-assign]
    with pytest.raises(httpx.ConnectError):
        read_fingerprint(client, "col")

    # E o mesmo para a leitura de dimensao, que tambem devolve None.
    client.get_collection = _offline  # type: ignore[method-assign]
    with pytest.raises(httpx.ConnectError):
        describe_dense_size(client, "col")


def test_stamp_grava_identidade_distinta_por_collection():
    """Duas collections no mesmo embedding sao entradas distintas - e' o que
    permite auditar 'quem foi indexado com o que' collection a collection."""
    client = FakeClient(FakeInfo())
    stamp_collection(client, "sap_incident_docs", "m")
    stamp_collection(client, "sap_reference_library", "m")
    assert read_fingerprint(client, "sap_incident_docs") is not None
    assert read_fingerprint(client, "sap_reference_library") is not None
    assert len(client.points) == 2


def test_apos_stamp_a_verificacao_passa(caplog):
    """O ciclo completo que resolve o estado 'desconhecido': uma collection
    legada recebe identidade no proximo ingest, e passa a ser verificavel
    definitivamente. Este e' o caminho que evita exigir --reset (22 GB)
    so para poder ganhar um metadado."""
    reset_verification_cache()
    model = "nomic-embed-text"
    client = FakeClient()

    with caplog.at_level("WARNING", logger="app.rag.embedding_guard"):
        verify_once(client, "col", model)
    assert any("nao tem identidade de embedding" in r.message for r in caplog.records)

    # proximo ingest grava a identidade...
    stamp_collection(client, "col", model)

    # ...e a partir dai a verificacao fica definitiva e silenciosa.
    caplog.clear()
    reset_verification_cache()
    with caplog.at_level("WARNING", logger="app.rag.embedding_guard"):
        verify_once(client, "col", model)
    assert not [r for r in caplog.records if "nao tem identidade" in r.message]


def test_guard_nao_depende_de_metadata_na_collection(caplog):
    """Regressao do no-op silencioso: o `CollectionInfo` do qdrant-client
    1.19 nao tem atributo `metadata`, e o servidor ignora
    `update_collection(metadata=...)`. Um guard que le `metadata` cairia
    sempre no estado AVISA sem nunca detectar divergencia - nem mesmo
    depois de um `stamp_collection` bem-sucedido."""
    reset_verification_cache()
    model = "nomic-embed-text"
    client = FakeClient(FakeInfo(size=768))
    assert not hasattr(client.get_collection("col"), "metadata")

    stamp_collection(client, "col", "mxbai-embed-large")

    with pytest.raises(EmbeddingMismatchError):
        verify_once(client, "col", model)
