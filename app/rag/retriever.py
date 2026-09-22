"""Consulta (retrieval) nas bases de conhecimento indexadas no Qdrant.

Hybrid search na collection de incidentes: combina busca DENSA
(similaridade semantica via embeddings) com busca ESPARSA (BM25,
correspondencia de termos exatos - codigos de erro, nomes de
transacao) via fusao RRF nativa do Qdrant.

Decisao de score: a fusao RRF seleciona os melhores candidatos, mas
o 'score' retornado para cada hit e recalculado como similaridade de
cosseno DENSA pura contra o vetor da query - preserva a semantica de
confianca ja usada por score_threshold, guardrails e pelos testes
existentes (calibrados para cosseno, nao para escala RRF).
"""

import logging
from functools import lru_cache

import numpy as np
from fastembed import SparseTextEmbedding
from langchain_ollama import OllamaEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.models import Fusion, FusionQuery, Prefetch, SparseVector
from sentence_transformers import CrossEncoder

from app.config import settings

_logger = logging.getLogger(__name__)

EMBEDDING_MODEL = settings.embedding_model
SPARSE_MODEL_NAME = "Qdrant/bm25"
QDRANT_URL = settings.qdrant_url

DEFAULT_SCORE_THRESHOLD = 0.5
HYBRID_PREFETCH_LIMIT = 20  # candidatos por perna (dense/sparse) antes da fusao
RERANKER_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"  # DA-29: mmarco supera baseline em +7pp Hit@1, +4pp MRR@5, 3.5x mais rapido
RERANKER_TOP_K = 3  # quantos candidatos retornar apos o reranking

# DA-25: piso baixo aplicado ANTES do reranker - so descarta ruido
# semantico extremo (candidato sem nenhuma relacao com a query), nunca
# a decisao real de confianca. Antes, DEFAULT_SCORE_THRESHOLD (0.5) era
# aplicado aqui e descartava candidatos com BM25/RRF forte mas cosseno
# denso moderado (ex: 0.47) ANTES do cross-encoder ter qualquer chance
# de avaliar a relevancia semantica de verdade - um documento poderia
# ter o termo exato certo (ex: "IDoc status 51") e ainda assim nunca
# chegar ao reranker. Ver _evidence_admission_score() para onde a
# decisao de confianca de verdade agora acontece (pos-reranking).
MIN_CANDIDATE_FLOOR = 0.05

COLLECTIONS = {
    "incidents": "sap_incident_docs",
    "reference": "sap_reference_library",
}
HYBRID_TARGETS = {"incidents"}  # so a base de diagnostico usa hybrid


@lru_cache(maxsize=1)
def _get_qdrant_client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL)


@lru_cache(maxsize=1)
def _get_embeddings() -> OllamaEmbeddings:
    return OllamaEmbeddings(model=EMBEDDING_MODEL, base_url=settings.ollama_host)


@lru_cache(maxsize=1)
def _get_sparse_model() -> SparseTextEmbedding:
    return SparseTextEmbedding(model_name=SPARSE_MODEL_NAME)


def _sparse_query_vector(query: str) -> SparseVector:
    embedding = next(_get_sparse_model().embed([query]))
    return SparseVector(indices=embedding.indices.tolist(), values=embedding.values.tolist())


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    a_arr, b_arr = np.array(a), np.array(b)
    denom = np.linalg.norm(a_arr) * np.linalg.norm(b_arr)
    if denom == 0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / denom)


def _retrieve_hybrid(query: str, collection_name: str, top_k: int) -> list[dict]:
    client = _get_qdrant_client()
    dense_query = _get_embeddings().embed_query(query)
    sparse_query = _sparse_query_vector(query)

    # DA-25: pool de candidatos da fusao RRF maior que top_k - antes,
    # limit=top_k aqui truncava a fusao para so 3 candidatos ANTES de
    # qualquer filtragem/reranking, entao o reranker nunca via mais
    # candidatos do que o resultado final ja ia ter mesmo assim. Um
    # pool maior deixa o cross-encoder (mais preciso) realmente
    # escolher entre mais opcoes.
    fusion_limit = max(top_k * 5, HYBRID_PREFETCH_LIMIT)

    fused = client.query_points(
        collection_name=collection_name,
        prefetch=[
            Prefetch(query=dense_query, using="dense", limit=HYBRID_PREFETCH_LIMIT),
            Prefetch(query=sparse_query, using="sparse", limit=HYBRID_PREFETCH_LIMIT),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=fusion_limit,
        with_vectors=["dense"],
    ).points

    # Score composto: combina cosseno denso (semantica) com o score RRF
    # devolvido pelo Qdrant (que captura contribuicao do BM25 esparso).
    # alpha=0.7: semantica tem peso maior, mas BM25 ainda influencia
    # quando um termo exato (codigo de erro, nome de transacao) aparece
    # no documento mas nao no espaco semantico do embedding.
    #
    # Nota: o Qdrant ja executa a fusao RRF real internamente via
    # FusionQuery(fusion=Fusion.RRF) e devolve hit.score na escala
    # correta. Re-derivar o score por posicao em fused (enumerate) seria
    # "double ranking" - aplicar RRF sobre output de RRF - o que e
    # matematicamente incorreto. Usamos hit.score diretamente.
    ALPHA = 0.7

    results = []
    for rank, hit in enumerate(fused):
        stored_dense = hit.vector["dense"] if isinstance(hit.vector, dict) else hit.vector
        cosine_score = _cosine_similarity(dense_query, stored_dense)

        # hit.score e o score RRF verdadeiro calculado pelo Qdrant
        # internamente (por lista densa e esparsa separadas) - usamos
        # diretamente como sinal de rank esparso.
        qdrant_rrf_score = hit.score

        composite_score = ALPHA * cosine_score + (1 - ALPHA) * qdrant_rrf_score

        # DA-25: so descarta ruido extremo aqui (MIN_CANDIDATE_FLOOR) -
        # a decisao real de confianca (score_threshold) acontece DEPOIS
        # do reranker, em _evidence_admission_score(). Ver comentario em
        # MIN_CANDIDATE_FLOOR acima.
        if cosine_score < MIN_CANDIDATE_FLOOR:
            continue

        results.append(
            {
                "source": hit.payload.get("source"),
                "text": hit.payload.get("text"),
                # "score" = cosseno puro, nao o composto - contrato
                # documentado no topo do modulo (guardrails, thresholds
                # e oos_rejection do eval sao calibrados para cosseno;
                # devolver o composto aqui inflava OOS queries genericas
                # com o bonus fixo de RRF do rank 0, mesmo sem match real).
                "score": cosine_score,
                "cosine_score": cosine_score,
                "composite_score": composite_score,
                "rrf_rank": rank,
                "collection": collection_name,
            }
        )
    # Ordena por composite_score (aproveita o sinal do BM25/RRF para
    # priorizar match de termo exato), mas o "score" reportado
    # continua sendo cosseno puro - so a ORDEM de candidatos usa RRF,
    # nao a confianca comunicada para o resto do pipeline.
    results.sort(key=lambda r: r["composite_score"], reverse=True)
    return results


def _retrieve_dense_only(
    query: str, collection_name: str, top_k: int, score_threshold: float
) -> list[dict]:
    client = _get_qdrant_client()
    query_vector = _get_embeddings().embed_query(query)
    results = client.query_points(
        collection_name=collection_name,
        query=query_vector,
        limit=top_k,
        score_threshold=score_threshold,
    ).points
    return [
        {
            "source": hit.payload.get("source"),
            "text": hit.payload.get("text"),
            "score": hit.score,
            "cosine_score": hit.score,
            "rrf_rank": None,
            "collection": collection_name,
        }
        for hit in results
    ]


@lru_cache(maxsize=1)
def _get_reranker() -> CrossEncoder:
    """Carrega o cross-encoder de reranking (cache — carrega uma vez por processo).

    Modelo: mmarco-mMiniLMv2-L12-H384-v1 (multilingual, ~120MB) — escolhido
    na DA-29 por superar o baseline ms-marco-MiniLM-L-6-v2 em +7pp Hit@1 e
    +4pp MRR@5 nos incidentes SAP/integração em PT-BR/EN, sendo 3.5x mais
    rapido que modelos L-12 em FP32 (ver docs/RERANKER_BENCHMARK.md).
    """
    return CrossEncoder(RERANKER_MODEL)


def rerank(query: str, hits: list[dict], top_k: int = RERANKER_TOP_K) -> list[dict]:
    """Reranqueia candidatos RAG usando cross-encoder semantico.

    O cross-encoder avalia cada par (query, chunk) individualmente —
    muito mais preciso que similaridade de cosseno, que avalia query
    e chunk de forma independente no espaco de embeddings.

    O score do reranker substitui o score composto RRF para a ordenacao
    final, mas o score cosseno original e preservado para os guardrails
    (que sao calibrados para escala cosseno).
    """
    if not hits:
        return hits

    reranker = _get_reranker()
    pairs = [(query, h["text"]) for h in hits]
    scores = reranker.predict(pairs)

    for hit, score in zip(hits, scores):
        hit["rerank_score"] = float(score)

    reranked = sorted(hits, key=lambda h: h["rerank_score"], reverse=True)
    return reranked[:top_k]


def _evidence_admission_score(hit: dict) -> float:
    """DA-25: decide se um candidato reranqueado tem evidencia forte o
    suficiente para ser admitido na resposta final. Usa o MAIOR entre o
    cosseno denso (match semantico direto, escala em que
    score_threshold ja e calibrado) e o rerank_score do cross-encoder
    normalizado/clampado para [0, 1] (mesma convencao ja usada em
    _compute_evidence_strength, app/agent/nodes.py) - um documento pode
    provar relevancia por QUALQUER UM dos dois caminhos, nao so pelo
    cosseno.

    Corrige o caso relatado na revisao externa: um documento com BM25
    excelente (match de termo exato, ex: "IDoc status 51") mas cosseno
    denso moderado (ex: 0.47) era descartado por score_threshold=0.5
    ANTES do reranker (mais preciso, avalia o par query+chunk de
    verdade) ter qualquer chance de opinar. Agora, se o reranker
    considerar o par fortemente relevante, o documento e admitido
    mesmo com cosseno abaixo do threshold.
    """
    rerank_score = hit.get("rerank_score")
    scores = [hit["score"]]
    if rerank_score is not None:
        scores.append(max(0.0, min(1.0, rerank_score)))
    return max(scores)


def _retrieve_unified(
    query: str,
    top_k: int,
    score_threshold: float,
) -> list[dict]:
    """Busca em incidents (hybrid dense+BM25); so consulta a
    reference_library como fallback quando incidents nao cobre a query
    (ver DA-17 abaixo) - nunca em paralelo/concorrendo de igual pra
    igual. O score retornado e sempre cosseno denso (mesma escala dos
    guardrails existentes).
    """

    # DA-17: reference_library so participa como FALLBACK, quando
    # incidents nao cobre a query - nunca como competidor de igual pra
    # igual. O docstring desta funcao ja prometia isso ("enriquece
    # quando os documentos de troubleshooting nao cobrem o incidente
    # com precisao suficiente"), mas o codigo original ignorava essa
    # regra e fundia as duas colecoes por score bruto sempre - um
    # manual generico (ex: Manual_Basis_SAP_R3.pdf, denso em texto
    # tecnico correlato) podia vencer um documento de incidente feito
    # sob medida so por ter mais massa textual similar em busca densa
    # pura. Uma penalidade multiplicativa (tentativa anterior) reduzia
    # o problema mas nao o eliminava, porque o reranker cross-encoder
    # reavalia o texto e pode preferir o manual de qualquer forma. A
    # regra objetiva e mais forte: so consulta reference_library quando
    # incidents NAO retornou nada acima do score_threshold.
    # A reference_library nao e curada por incidente (766k+ chunks de
    # manuais tecnicos genericos) - qualquer query relacionada a SAP
    # tende a achar ALGO semanticamente proximo nela, mesmo quando o
    # incidente reportado nao tem relacao real com nenhum documento
    # conhecido (caso out-of-scope). Por isso o fallback exige um
    # score bem mais alto que o usado em incidents (documentos feitos
    # sob medida): 0.85 filtra "vagamente parecido" e so deixa passar
    # match forte o suficiente para ser confiavel como fallback.
    REFERENCE_FALLBACK_THRESHOLD = 0.85

    incidents_hits = _retrieve_hybrid(query, COLLECTIONS["incidents"], top_k)
    all_hits: list[dict] = list(incidents_hits)

    # DA-25: incidents_hits ja nao vem pre-filtrado por score_threshold
    # (so pelo MIN_CANDIDATE_FLOOR de ruido extremo) - o gatilho do
    # fallback para reference_library continua olhando para o MELHOR
    # candidato de incidents por cosseno (mesmo criterio de antes),
    # so que agora incidents_hits pode conter candidatos fracos que
    # antes eram descartados cedo demais; eles ainda entram no pool
    # do reranker abaixo, so nao contam como "match forte" para decidir
    # se consulta reference_library tambem.
    incidents_has_strong_match = any(h["score"] >= score_threshold for h in incidents_hits)

    if not incidents_has_strong_match:
        client = _get_qdrant_client()
        try:
            ref_info = client.get_collection("sap_reference_library")
            if ref_info.points_count > 0:
                all_hits.extend(
                    _retrieve_dense_only(
                        query,
                        "sap_reference_library",
                        top_k,
                        REFERENCE_FALLBACK_THRESHOLD,
                    )
                )
        except (ValueError, RuntimeError) as _ref_err:
            # Distingue falha de infra (Qdrant inacessivel) de colecao vazia:
            # sem log, o diagnóstico parece correto quando na verdade o retrieval falhou.
            _logger.warning(
                "[retriever] sap_reference_library indisponivel — "
                "continuando sem contexto de referencia: %s",
                _ref_err,
            )

    # Funde por score (cosseno denso ja normalizado 0-1)
    # Remove duplicatas por source+text, mantendo o maior score
    seen: dict[str, dict] = {}
    for hit in all_hits:
        key = f"{hit['source']}::{hit['text'][:100]}"
        if key not in seen or hit["score"] > seen[key]["score"]:
            seen[key] = hit

    results = sorted(seen.values(), key=lambda r: r["score"], reverse=True)
    candidates = results[: top_k * 5]  # passa mais candidatos pro reranker

    # DA-25: reranqueia TODOS os candidatos do pool (nao so os top_k),
    # mesmo quando ha apenas 1 - um unico candidato com cosseno fraco
    # e exatamente o caso que esta correcao existe para salvar (BM25
    # forte, cosseno moderado); pular o reranker so por ter 1 candidato
    # o deixaria sem chance de ser resgatado pelo cross-encoder. O
    # corte final agora acontece DEPOIS do reranker opinar
    # (_evidence_admission_score), nao antes. rerank() ja ordena por
    # rerank_score internamente.
    candidates = rerank(query, candidates, top_k=len(candidates))

    admitted = [c for c in candidates if _evidence_admission_score(c) >= score_threshold]
    return admitted[:top_k]


def retrieve(
    query: str,
    target: str = "incidents",
    top_k: int = 3,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
) -> list[dict]:
    """Retorna ate top_k chunks mais relevantes para a query.

    Para o target 'incidents', usa hybrid search (dense + sparse BM25,
    fusao RRF) - melhora recall para queries com termos exatos (codigos
    de erro, nomes de transacao) que busca puramente semantica pode
    perder. Outros targets continuam com busca densa pura.
    """
    collection_name = COLLECTIONS[target]

    if target in HYBRID_TARGETS:
        # v2.0: retrieval unificado — incidents + reference_library em paralelo
        return _retrieve_unified(query, top_k, score_threshold)
    return _retrieve_dense_only(query, collection_name, top_k, score_threshold)


if __name__ == "__main__":
    import sys

    target = "incidents"
    args = sys.argv[1:]
    if args and args[0] in ("incidents", "reference"):
        target = args[0]
        args = args[1:]

    query = " ".join(args) or "iFlow falhando com timeout"
    print(f"[{target}] Query: {query}\n")
    hits = retrieve(query, target=target)
    if not hits:
        print(f"(nenhum resultado acima do score_threshold={DEFAULT_SCORE_THRESHOLD})")
    for i, hit in enumerate(hits, start=1):
        print(f"--- resultado {i} (score={hit['score']:.4f}, fonte={hit['source']}) ---")
        print(hit["text"][:300])
        print()
