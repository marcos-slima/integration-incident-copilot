"""DA-45: identidade do embedding, para nao comparar vetores nao comparaveis.

O PROBLEMA QUE A DIMENSAO NAO PEGA

Trocar `embedding_model` no `.env` e' uma mudanca de uma linha que
parece inofensiva. No Qdrant, ela NAO e' inofensiva: a collection
continua la, com os MESMOS pontos, e o sistema passa a produzir
resultados plausiveis e ERRADOS. Um `nomic-embed-text` (768) e um
`mxbai-embed-large` (768) tem a mesma dimensao e ruang vetorial
completamente diferente - a busca passa a comparar embeddings de um
modelo contra embeddings de outro, e devolve silencio com confianca
alta em vez de erro.

Por isso o guard existente em ingest.py, que so checa `size`, e'
insuficiente por construcao: dimensao igual nao implica embedding igual.
Como 768 e' a dimensao mais comum do ecossistema, essa colisao nao e'
exotica - e' o caso comum.

A ABORDAGEM

Gravar a identidade do modelo e verifica-la antes de usar. Tres estados,
e o terceiro e' o honesto:

| Estado                        | Significado                              | Acao |
|-------------------------------|------------------------------------------|------|
| fingerprint bate              | mesma identidade                        | segue |
| fingerprint DIVERGE           | outra identidade, ou dimensao diferente  | FALHA |
| fingerprint AUSENTE           | collection anterior a DA-45              | AVISA |

O terceiro estado e' o ponto honesto do modulo. As collections deste
projeto (40 + 28.962 pontos) foram ingestadas antes de a identidade
existir, e nao ha como saber retroativamente qual modelo as gerou. Um
guard que tratasse "desconhecido" como "ok" seria exatamente o bug que
este modulo existe para evitar; um guard que tratasse como "erro"
derrubaria um corpus de 22 GB por causa de metadado ausente. Entao o
comportamento e' dizer que NAO SABE, dizer por que isso importa, e
oferecer o comando de reindexacao. O proximo ingest ja grava a
identidade, e a partir dai a verificacao passa a ser definitiva.

ONDE A IDENTIDADE VIVE (e por que nao e' no metadata da collection)

A primeira versao gravava em `collection.metadata`, via
`update_collection(metadata=...)`. Isso NAO FUNCIONA no Qdrant 1.19, e
falhava em SILENCIO - o tipo `CollectionInfo` do qdrant-client 1.19.0
nao tem o campo `metadata`, e o servidor aceita o PATCH, responde
`{"result": true, "status": "ok"}` e nao persiste nada. O `GET` seguinte
continua devolvendo `metadata: null`.

A falha era invisível pelos tres lados: `read_fingerprint` usava
`getattr(info, "metadata", None)`, que devolve `None` tanto para "nao
gravado" quanto para "campo que este cliente nao conhece". Entao o guard
caia sempre no estado AVISA, e o teste passava porque `FakeInfo` tinha o
atributo que o cliente real nao tem. Um guard que nunca consegue
distinguir "desconhecido" de "quebrado" nao e' um guard.

A identidade passa a morar numa collection lateral,
`iic_collection_identity`, com UM ponto por collection auditada. E' o
mesmo armazenamento que ja guarda o resto do estado do Qdrant, entao:

- sobrevive a restart do container (esta no mesmo volume);
- e' lida com `retrieve` por id, que ja e' uma operacao suportada e
  barata - diferente de `metadata`, que este cliente nao le;
- some junto com o `docker compose down -v`, que e' o comportamento
  desejado: um banco recriado do zero nao pode herdar identidade de
  vetores que nao existem mais;
- da para inspecionar com um `curl` e verificar a olho, o que o
  `metadata` tambem nao permitia.

O registro tem um vetor placeholder de 1 dimensao porque o Qdrant
exige o campo `vector` em TODO ponto (um ponto sem ele e' rejeitado com
`missing field vector`, tanto no cliente quanto no servidor). O valor do
vetor nao significa nada: quem le a identidade so olha o `payload`.
"""

from __future__ import annotations

import hashlib
import logging
from uuid import NAMESPACE_URL, uuid5

from qdrant_client.http import models
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.http.models import Distance, VectorParams

_logger = logging.getLogger(__name__)

#: Collection lateral que guarda a identidade de embedding de cada
#: collection auditada. Um ponto por collection, id deterministico.
IDENTITY_COLLECTION = "iic_collection_identity"

#: Largura do vetor placeholder do registro de identidade. O Qdrant exige
#: o campo `vector` em todo ponto, entao o registro precisa de um; 1 e' a
#: menor largura que a API aceita.
IDENTITY_VECTOR_SIZE = 1

#: Chave do payload dentro da collection lateral. Versionada para
#: permitir migracao futura de esquema sem colidir com um valor antigo.
FINGERPRINT_KEY = "iic_embedding_fingerprint_v1"

#: Versao do esquema de metadado. Se a forma do fingerprint mudar, este
#: numero sobe e as collections antigas sao tratadas como "sem
#: fingerprint" (o caminho honesto), nunca como "compativeis".
FINGERPRINT_SCHEMA = "v1"


class EmbeddingMismatchError(RuntimeError):
    """A collection foi indexada com outro embedding.

    Erro, e nao warning: buscar sobre vetores de outra identidade nao
    degrada - ela INVENTA resposta, e uma resposta inventada com
    confianca alta e' pior que indisponibilidade (DA-3: guardrail em
    codigo, nao na confianca do LLM).
    """


def embedding_fingerprint(model: str, provider: str = "ollama") -> str:
    """Identidade estavel e opaca do par (provider, modelo).

    Opaca de proposito: e' gravada em metadata que aparece em
    `GET /collections` e em logs de suporte. Um hash de 12 caracteres
    identifica a combinacao sem publicar o nome do modelo. Estavel
    porque e' derivada so do par - dois processos com o mesmo `.env`
    produzem o mesmo valor, que e' o que torna a verificacao util.
    """
    bruto = f"{FINGERPRINT_SCHEMA}|{provider.strip().lower()}|{model.strip()}"
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:12]


def identity_point_id(collection_name: str) -> str:
    """Id deterministico do ponto de identidade de uma collection.

    Determinismo importa por dois motivos: `stamp_collection` pode ser
    chamado em todo ingest sem duplicar registro, e o ponto nao muda de
    id quando o `points_count` da collection auditada muda - que e' o
    que aconteceria com um id incremental.

    O Qdrant aceita so id INTEIRO ou UUID (`value abc123 is not a valid
    point ID`), entao um hash hexadecimal cru seria rejeitado na porta.
    `uuid5` sobre o namespace do projeto satisfaz as duas coisas.
    """
    return str(uuid5(NAMESPACE_URL, f"{IDENTITY_COLLECTION}|{collection_name}"))


def _ensure_identity_collection(client) -> None:
    """Cria a collection lateral de identidade, se ainda nao existir.

    A collection tem vetor de 1 dimensao porque o Qdrant exige o campo
    `vector` em TODO ponto - um ponto sem ele e' rejeitado com
    "missing field `vector`" tanto no cliente quanto no servidor. O
    registro nao e' um vetor de conteudo: o valor `[0.0]` e' um
    placeholder de largura minima, e `read_fingerprint` so le o
    `payload`. Uma dimensao e' o menor custo que a API permite.

    Uma collection vector-less NAO e' aproveitavel: o `PATCH` do Qdrant
    nao adiciona um vetor a uma collection que nao tem ("Not existing
    vector name"), entao o unico caminho de um estado assim e' recriar.
    Isso nao e' um caso teorico - e' o que uma versao anterior deste
    modulo (que criava a collection sem `vectors_config`) deixa para
    tras, e sem este reparo o `stamp_collection` falharia para sempre
    com 400, levando o ingest inteiro junto. A collection lateral so
    guarda registro, entao recria-la nao perde nada.
    """
    try:
        client.create_collection(
            collection_name=IDENTITY_COLLECTION,
            vectors_config=VectorParams(size=IDENTITY_VECTOR_SIZE, distance=Distance.COSINE),
        )
        _logger.debug("collection de identidade criada: %s", IDENTITY_COLLECTION)
        return
    except Exception as exc:  # pragma: no cover - depende do cliente
        # Ja existir e' o caso normal (todo ingest apos o primeiro).
        # `UnexpectedResponse` e' o erro do Qdrant para "ja existe"; o
        # nome ja mudou entre versoes, entao a checagem e' por texto
        # em vez de por tipo.
        if "already exist" not in str(exc).lower():
            raise

    if describe_dense_size(client, IDENTITY_COLLECTION) == IDENTITY_VECTOR_SIZE:
        return

    _logger.warning(
        "collection de identidade '%s' tem schema incompativel (esperado vetor de "
        "%d dim); recriando. Ela so guarda registro, entao nada se perde.",
        IDENTITY_COLLECTION,
        IDENTITY_VECTOR_SIZE,
    )
    client.delete_collection(IDENTITY_COLLECTION)
    client.create_collection(
        collection_name=IDENTITY_COLLECTION,
        vectors_config=VectorParams(size=IDENTITY_VECTOR_SIZE, distance=Distance.COSINE),
    )


def read_fingerprint(client, collection_name: str) -> str | None:
    """Fingerprint gravado para `collection_name`, ou None se ausente.

    Devolve None quando a collection lateral ainda nao existe (projeto
    anterior a DA-45) e tambem quando o ponto da collection auditada nao
    foi gravado. Os dois casos sao "nao sei", e nenhum dos dois e'
    "sei e nao pode comparar".
    """
    try:
        point = client.retrieve(
            collection_name=IDENTITY_COLLECTION,
            ids=[identity_point_id(collection_name)],
            with_payload=True,
            with_vectors=False,
        )
    except UnexpectedResponse:
        # Collection lateral ausente: o Qdrant responde 404 (ou 400 quando
        # a collection nem existe ainda, verificado na 1.19.0). Ausente e'
        # "nao sei", nao erro - quem decide a severidade e'
        # `verify_collection_embedding`.
        #
        # Nao e' `except Exception`: rede caida seria mascarada como
        # "sem fingerprint", que e' o estado que emite o AVISO de
        # identidade desconhecida. Um Qdrant inalcancavel precisa
        # aparecer como falha de infraestrutura, nao como corpus sem
        # registro.
        return None

    if not point:
        return None
    value = (point[0].payload or {}).get(FINGERPRINT_KEY)
    return value if isinstance(value, str) and value else None


def describe_dense_size(client, collection_name: str) -> int | None:
    """Dimensao do vetor denso, aceitando os dois formatos de schema.

    O Qdrant aceita collection densa com vetor sem nome (`vectors` direto)
    ou nomeado (`vectors={"dense": ...}`) - e' o mesmo `ensure_collection`
    de ingest.py que trata os dois casos.

    Devolve None quando a collection nao existe. Isso e' distinto de
    "existe com dimensao 0": a primeira e' ausencia de informacao, a
    segunda seria um schema invalido.
    """
    try:
        collection_info = client.get_collection(collection_name)
    except UnexpectedResponse:
        return None
    vectors = getattr(getattr(collection_info, "config", None), "params", None)
    vectors = getattr(vectors, "vectors", None)
    dense = vectors.get("dense") if isinstance(vectors, dict) else vectors
    size = getattr(dense, "size", None)
    return size if isinstance(size, int) else None


def verify_collection_embedding(
    client,
    collection_name: str,
    expected_model: str,
    expected_size: int | None = None,
    provider: str = "ollama",
) -> str | None:
    """Verifica a identidade do embedding de uma collection existente.

    Devolve None quando esta tudo bem, ou uma mensagem de AVISO quando
    a identidade e' desconhecida. Levanta `EmbeddingMismatchError` quando
    ha divergencia comprovada.

    Deliberadamente NAO faz probe de embedding: quem chama decide se tem
    o modelo carregado. Um guard que sobe `Ollama` para descobrir a
    dimensao transformaria uma verificacao de registro em um boot lento
    e dependente de infra.

    `client` e' o QdrantClient, e nao o `CollectionInfo`: a identidade
    mora na collection lateral (ver `read_fingerprint`), entao a
    verificacao precisa do cliente para le-la. A dimensao, essa sim,
    continua vindo do `CollectionInfo` - e' o que a checagem de schema
    usa, e ela e' lida internamente.
    """
    atual = read_fingerprint(client, collection_name)
    esperado = embedding_fingerprint(expected_model, provider)

    if atual == esperado:
        return None

    size_atual = describe_dense_size(client, collection_name)

    # Divergencia de dimensao e' prova suficiente sozinha: nem a
    # identidade nem o schema batem, e nao ha caso legitimo em que isso
    # seja acidental.
    if expected_size is not None and size_atual is not None and size_atual != expected_size:
        raise EmbeddingMismatchError(
            f"Collection '{collection_name}' tem vetores de dimensao {size_atual}, "
            f"mas o embedding configurado ('{expected_model}') produz "
            f"{expected_size}. Os dados indexados NAO servem para este modelo. "
            f"Reindexe: uv run python -m app.rag.ingest --reset"
        )

    if atual is not None and atual != esperado:
        raise EmbeddingMismatchError(
            f"Collection '{collection_name}' foi indexada com um embedding de "
            f"identidade '{atual}', e a configuracao atual ('{expected_model}') "
            f"produz '{esperado}'. Dimensao pode ser a mesma ({size_atual}) e "
            f"ainda assim os vetores nao sao comparaveis - e' exatamente o "
            f"caso que a checagem de dimensao sozinha nao pega. "
            f"Reindexe: uv run python -m app.rag.ingest --reset"
        )

    # Sem fingerprint: nao da para saber. Diz isso, em vez de passar
    # silencio - que e' o que a checagem de dimensao fazia.
    return (
        f"Collection '{collection_name}' nao tem identidade de embedding "
        f"gravada (foi indexada antes da DA-45), entao NAO da para confirmar "
        f"que foi gerada com '{expected_model}'. Dimensao "
        f"{size_atual} pode coincidir e ainda assim os vetores serem de outro "
        f"modelo - a comparacao fica silenciosamente errada. Se esta collection "
        f"foi de fato indexada com o embedding atual, um reindex sem --reset "
        f"gravara a identidade e a verificacao passa a ser definitiva."
    )


def stamp_collection(client, collection_name: str, model: str, provider: str = "ollama") -> None:
    """Grava a identidade do embedding na collection lateral.

    Usa `upsert` com id deterministico, e nao `update_collection`: o
    Qdrant 1.19 aceita e IGNORA `metadata` (ver a nota no topo do
    modulo), entao a via antiga nunca confirmava a gravacao. Aqui a
    gravacao e' um ponto de verdade - o `upsert` devolve o estado do
    ponto, e o `read_fingerprint` seguinteconsegue le-lo de volta.

    Chamado na criacao e a cada ingest, para que a collection que existe
    hoje vire a collection verificavel de amanha.
    """
    fingerprint = embedding_fingerprint(model, provider)
    _ensure_identity_collection(client)
    client.upsert(
        collection_name=IDENTITY_COLLECTION,
        points=[
            models.PointStruct(
                id=identity_point_id(collection_name),
                vector=[0.0] * IDENTITY_VECTOR_SIZE,
                payload={
                    "collection": collection_name,
                    FINGERPRINT_KEY: fingerprint,
                    "provider": provider.strip().lower(),
                    "embedding_model": model,
                    "dense_size": describe_dense_size(client, collection_name),
                },
            )
        ],
    )
    _logger.debug(
        "embedding fingerprint gravado: collection=%s fingerprint=%s",
        collection_name,
        fingerprint,
    )


#: Collections ja verificadas neste processo. O guard e' uma ida ao
#: Qdrant (`retrieve` na collection lateral + `get_collection` para a
#: dimensao); no caminho quente da retrieval ele nao pode virar uma ida
#: extra POR QUERY. Como o par (collection, modelo) nao muda durante a
#: vida do processo, verificar uma vez e' suficiente - um ingest
#: concorrente que troque a identidade no meio seria uma operacao
#: administrativa, nao um cenario de runtime.
_VERIFIED: set[tuple[str, str]] = set()


def verify_once(
    client,
    collection_name: str,
    model: str,
    expected_size: int | None = None,
    provider: str = "ollama",
) -> None:
    """Verifica a identidade do embedding no máximo uma vez por processo.

    Repete o aviso de collection sem fingerprint a cada verificacao real
    (nao a cada query) - o log nao pode ser inundado, e a ausencia da
    identidade continua visivel no primeiro acesso de cada processo.
    """
    chave = (collection_name, embedding_fingerprint(model, provider))
    if chave in _VERIFIED:
        return
    aviso = verify_collection_embedding(
        client,
        collection_name,
        expected_model=model,
        expected_size=expected_size,
        provider=provider,
    )
    # So entra no cache DEPOIS de verificar (validacao 2026-10-07, R15):
    # antes a chave era gravada antes da checagem, entao a 1a chamada
    # levantava EmbeddingMismatchError e todas as seguintes passavam em
    # silencio contra a collection incompativel.
    _VERIFIED.add(chave)
    if aviso:
        _logger.warning("DA-45 embedding: %s", aviso)


def reset_verification_cache() -> None:
    """Limpa o cache. Para teste, e para um ingest que acabou de
    reindexar dentro do mesmo processo."""
    _VERIFIED.clear()
