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

Gravar a identidade do modelo no METADATA da collection, e verifica-la
antes de usar. Tres estados, e o terceiro e' o honesto:

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
"""

from __future__ import annotations

import hashlib
import logging

_logger = logging.getLogger(__name__)

#: Chave no metadata da collection. Versionada para permitir migracao
#: futura de esquema sem colidir com um valor antigo.
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


def read_fingerprint(collection_info) -> str | None:
    """Fingerprint gravado na collection, ou None se ausente."""
    metadata = getattr(collection_info, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    value = metadata.get(FINGERPRINT_KEY)
    return value if isinstance(value, str) and value else None


def describe_dense_size(collection_info) -> int | None:
    """Dimensao do vetor denso, aceitando os dois formatos de schema.

    O Qdrant aceita collection densa com vetor sem nome (`vectors` direto)
    ou nomeado (`vectors={"dense": ...}`) - e' o mesmo `ensure_collection`
    de ingest.py que trata os dois casos.
    """
    vectors = getattr(getattr(collection_info, "config", None), "params", None)
    vectors = getattr(vectors, "vectors", None)
    dense = vectors.get("dense") if isinstance(vectors, dict) else vectors
    size = getattr(dense, "size", None)
    return size if isinstance(size, int) else None


def verify_collection_embedding(
    collection_info,
    collection_name: str,
    expected_model: str,
    expected_size: int | None = None,
    provider: str = "ollama",
) -> str | None:
    """Verifica a identidade do embedding de uma collection existente.

    Devolve None quando esta tudo bem, ou uma mensagem de AVISO quando
    a identidade e' desconhecida. Levanta `EmbeddingMismatchError` quando
    ha divergencia comprovada.

    Deliberadamente NAO faz chamada de rede nem probe de embedding: quem
    chama decide se tem o modelo carregado. Um guard que sobe `Ollama`
    para descobrir a dimensao transformaria uma verificacao de metadado
    em um boot lento e dependente de infra.
    """
    atual = read_fingerprint(collection_info)
    esperado = embedding_fingerprint(expected_model, provider)

    if atual == esperado:
        return None

    size_atual = describe_dense_size(collection_info)

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
    """Grava a identidade do embedding no METADATA da collection.

    Usa `update_collection`, e nao `set_payload`: payload sao atributos
    de PONTO, e a identidade do embedding e' uma propriedade da
    collection inteira. `set_payload` exigiria ids de ponto e gravaria
    a marca apenas nos pontos tocados - exatamente o contrario do que se
    quer, que e um verificador valido para a collection toda.

    Chamado na criacao e a cada ingest, para que a collection que existe
    hoje vire a collection verificavel de amanha.
    """
    fingerprint = embedding_fingerprint(model, provider)
    client.update_collection(
        collection_name=collection_name,
        metadata={FINGERPRINT_KEY: fingerprint},
    )
    _logger.debug(
        "embedding fingerprint gravado: collection=%s fingerprint=%s",
        collection_name,
        fingerprint,
    )


#: Collections ja verificadas neste processo. O guard e' uma ida ao
#: Qdrant (`get_collection`); no caminho quente da retrieval ele nao pode
#: virar uma ida extra POR QUERY. Como o par (collection, modelo) nao
#: muda durante a vida do processo, verificar uma vez e' suficiente -
#: um ingest concorrente que troque a identidade no meio seria uma
#: operacao administrativa, nao um cenario de runtime.
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
    _VERIFIED.add(chave)
    aviso = verify_collection_embedding(
        client.get_collection(collection_name),
        collection_name,
        expected_model=model,
        expected_size=expected_size,
        provider=provider,
    )
    if aviso:
        _logger.warning("DA-45 embedding: %s", aviso)


def reset_verification_cache() -> None:
    """Limpa o cache. Para teste, e para um ingest que acabou de
    reindexar dentro do mesmo processo."""
    _VERIFIED.clear()
