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
import os
from functools import lru_cache
from pathlib import Path

import numpy as np
from fastembed import SparseTextEmbedding, TextEmbedding
from langchain_ollama import OllamaEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Fusion, FusionQuery, Prefetch, SparseVector
from sentence_transformers import CrossEncoder

from app.config import settings
from app.rag.embedding_guard import verify_once

_logger = logging.getLogger(__name__)

EMBEDDING_MODEL = settings.embedding_model
SPARSE_MODEL_NAME = "Qdrant/bm25"
QDRANT_URL = settings.qdrant_url

DEFAULT_SCORE_THRESHOLD = 0.5
HYBRID_PREFETCH_LIMIT = 20  # candidatos por perna (dense/sparse) antes da fusao
# DA-29 (ver docstring de _get_reranker). Mantenha em UMA linha: o gate
# reranker_invariant le esta atribuicao por regex.
RERANKER_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
RERANKER_TOP_K = 3  # quantos candidatos retornar apos o reranking

# DA-17: gate denso do fallback para `sap_reference_library` — so consulta a
# reference quando `incidents` (curado) nao tem match forte. Constante de
# MODULO (nao local de _retrieve_unified) para o script de recalibracao
# conseguir ler o valor em uso: scripts/calibrate_reference_fallback.py.
# Historico completo da medicao e a decisao ficam no comentario de
# _retrieve_unified, junto da logica que o aplica.
REFERENCE_FALLBACK_THRESHOLD = 0.665

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


# DA-38: suporte a EMBEDDING_BACKEND=fastembed para o CI de avaliacao RAG
# (job rag-quality no GitHub Actions nao tem Ollama disponivel). Em producao
# EMBEDDING_BACKEND nao e definido (default='ollama') e o comportamento e
# identico ao anterior.
_EMBEDDING_BACKEND = os.environ.get("EMBEDDING_BACKEND", "ollama").lower()


FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"


def embedding_identity() -> tuple[str, str]:
    """(provider, modelo) do embedder EFETIVO - o que de fato gera os vetores.

    Validacao 2026-10-07 (RAG-01/R15): ingest e retriever carimbavam e
    verificavam a collection sempre como ("ollama", EMBEDDING_MODEL), mesmo
    com EMBEDDING_BACKEND=fastembed indexando com bge-small. A identidade
    gravada mentia sobre o espaco vetorial, que e' exatamente o que o guard
    DA-45 existe para impedir."""
    if _EMBEDDING_BACKEND == "fastembed":
        return "fastembed", FASTEMBED_MODEL
    return "ollama", EMBEDDING_MODEL


class _FastEmbedWrapper:
    """Adaptador minimo de fastembed.TextEmbedding para a interface .embed_query()."""

    def __init__(self, model_name: str = FASTEMBED_MODEL) -> None:
        self._model = TextEmbedding(model_name=model_name)

    def embed_query(self, text: str) -> list[float]:
        return list(next(self._model.embed([text])))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # DA-38: a ingestao (`app.rag.ingest`) indexa com o MESMO embedder da
        # consulta, entao o adaptador tambem precisa saber indexar em lote.
        # Sem este metodo, `ingest` falhava com
        # "'_FastEmbedWrapper' object has no attribute 'embed_documents'" e o
        # job `rag-quality` do CI nao conseguia popular a collection.
        return [list(vector) for vector in self._model.embed(texts)]


@lru_cache(maxsize=1)
def _get_embeddings() -> "OllamaEmbeddings | _FastEmbedWrapper":
    if _EMBEDDING_BACKEND == "fastembed":
        _logger.info("EMBEDDING_BACKEND=fastembed: usando TextEmbedding local (sem Ollama)")
        return _FastEmbedWrapper()
    return OllamaEmbeddings(model=EMBEDDING_MODEL, base_url=settings.ollama_host)


def new_sparse_model() -> SparseTextEmbedding:
    """Instancia o BM25 do fastembed (usado na busca e na ingestao).

    Validacao 2026-10-07 (DEP-01): o fastembed 0.8.x nao carrega o
    `Qdrant/bm25` do cache offline - a descricao do modelo declara um
    `model_file` ficticio ("mock.file") que nunca existe no snapshot, e o
    teste de "esta no cache" falha sempre. Num cluster sem saida para o
    Hugging Face o retriever quebrava. `FASTEMBED_BM25_PATH` aponta para o
    snapshot ja baixado (a imagem Docker grava em /app/.cache/bm25); sem a
    variavel (ou com o diretorio vazio - imagem com PRELOAD_MODELS=0), o
    comportamento e o de sempre (download sob demanda).
    """
    local_path = os.environ.get("FASTEMBED_BM25_PATH", "").strip()
    if local_path and (Path(local_path) / "config.json").is_file():
        return SparseTextEmbedding(model_name=SPARSE_MODEL_NAME, specific_model_path=local_path)
    return SparseTextEmbedding(model_name=SPARSE_MODEL_NAME)


@lru_cache(maxsize=1)
def _get_sparse_model() -> SparseTextEmbedding:
    return new_sparse_model()


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
    # DA-45: um vetor de consulta de um modelo comparado com pontos de
    # outro devolve resposta plausivel e ERRADA, nao erro. Verificado uma
    # vez por processo (verify_once) para nao custar uma ida ao Qdrant por
    # query.
    _prov, _modelo = embedding_identity()
    verify_once(client, collection_name, _modelo, provider=_prov)
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

    # Score composto: combina cosseno denso (semantica) com rank posicional
    # normalizado (sinal do BM25 esparso via RRF do Qdrant).
    # alpha=0.7: semantica tem peso maior, mas BM25 ainda influencia
    # quando um termo exato (codigo de erro, nome de transacao) aparece
    # no documento mas nao no espaco semantico do embedding.
    #
    # Normalizacao do sinal RRF: hit.score do Qdrant esta na escala
    # 1/(60+rank) ≈ 0.015–0.016, entao com alpha=0.7/0.3 o BM25 contribuia
    # apenas ~0.005 no composite_score — efetivamente irrelevante para a
    # ordenacao. Substituimos por score posicional normalizado 1/(rank+1),
    # que varia em [1/N, 1] e preserva o sinal de rank sem escala tiny.
    # O rank aqui e da lista fused pos-RRF (ja fusionada), entao rank=0
    # = melhor candidato combinado denso+esparso.
    #
    # Contrato: hit.score original e preservado em rrf_raw_score para
    # observabilidade. "score" reportado continua sendo cosseno puro
    # (guardrails, thresholds e eval calibrados para escala cosseno).
    ALPHA = 0.7

    results = []
    for rank, hit in enumerate(fused):
        stored_dense = hit.vector["dense"] if isinstance(hit.vector, dict) else hit.vector
        cosine_score = _cosine_similarity(dense_query, stored_dense)

        # Normaliza para [1/N, 1]: rank=0 -> 1.0, rank=1 -> 0.5, ...
        # Preserva a ordem relativa da fusao RRF sem a escala tiny (0.015).
        rrf_normalized = 1.0 / (rank + 1)

        composite_score = ALPHA * cosine_score + (1 - ALPHA) * rrf_normalized

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
                "rrf_normalized": rrf_normalized,
                "rrf_raw_score": hit.score,
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
    # DA-45: mesma verificacao da perna hibrida - ver _retrieve_hybrid.
    _prov, _modelo = embedding_identity()
    verify_once(client, collection_name, _modelo, provider=_prov)
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

    Modelo: mmarco-mMiniLMv2-L12-H384-v1 (multilingual) — escolhido na DA-29.
    Re-medido em 2026-10-07 (M-20, 18 consultas in-scope): Hit@1 17/18 contra
    16/18 do baseline ms-marco-MiniLM-L-6-v2 - UMA consulta de diferenca,
    McNemar p=1.0, IC95% [+0.000, +0.167]: nao e uma vantagem demonstrada.
    Latencia media ~1,45x a do L-6 e ~0,74x a do L-12 (3,6x mais rapido so em
    relacao ao bge-reranker-base). A escolha se sustenta por ser
    multilingue (consultas em PT-BR), nao por "acertar mais". Ver
    docs/RERANKER_BENCHMARK.md e data/eval/reranker_benchmark_results.json.
    """
    return CrossEncoder(RERANKER_MODEL)


def _sigmoid_calibrate(raw_score: float) -> float:
    """DA-42: converte logit raw do CrossEncoder para probabilidade calibrada [0,1].

    CrossEncoder.predict() retorna logits (escala tipicamente -5 a +5,
    mas pode chegar a +/-10 em modelos fine-tuned). Clamp simples para
    [0,1] descarta toda informacao de scores negativos (todos viram 0)
    e comprime a escala positiva de forma nao-linear — um score 0.8 e
    um score 4.0 seriam tratados como iguais apos clamp.

    Sigmoid transforma a escala de logits para probabilidade de relevancia:
      sigma(0)  = 0.50  — incerto
      sigma(2)  = 0.88  — provavelmente relevante
      sigma(4)  = 0.98  — altamente relevante
      sigma(-2) = 0.12  — provavelmente irrelevante
    Preserva a monotonia (ordenacao de ranking nao muda) e produz uma
    escala semanticamente interpretavel para admission_score e evidence_strength.
    """
    return float(1.0 / (1.0 + np.exp(-raw_score)))


def rerank(query: str, hits: list[dict], top_k: int = RERANKER_TOP_K) -> list[dict]:
    """Reranqueia candidatos RAG usando cross-encoder semantico.

    O cross-encoder avalia cada par (query, chunk) individualmente —
    muito mais preciso que similaridade de cosseno, que avalia query
    e chunk de forma independente no espaco de embeddings.

    O score do reranker substitui o score composto RRF para a ordenacao
    final, mas o score cosseno original e preservado para os guardrails
    (que sao calibrados para escala cosseno).

    DA-42: rerank_score armazena o logit raw do CrossEncoder (para
    auditoria/debug); rerank_score_calibrated armazena sigma(logit),
    a probabilidade calibrada [0,1] usada em _evidence_admission_score
    e _compute_evidence_strength.
    """
    if not hits:
        return hits

    reranker = _get_reranker()
    pairs = [(query, h["text"]) for h in hits]
    raw_scores = reranker.predict(pairs)

    for hit, raw in zip(hits, raw_scores):
        hit["rerank_score"] = float(raw)  # logit raw (auditoria)
        hit["rerank_score_calibrated"] = _sigmoid_calibrate(float(raw))  # DA-42: prob [0,1]

    reranked = sorted(hits, key=lambda h: h["rerank_score_calibrated"], reverse=True)
    return reranked[:top_k]


def _evidence_admission_score(hit: dict) -> float:
    """DA-25/DA-42: score que decide se o candidato entra na resposta.

    Com o reranker disponivel, a decisao e' DELE (`rerank_score_calibrated`,
    sigmoid do logit do cross-encoder, ponto neutro 0.5). Sem reranker, vale
    o cosseno denso.

    Validacao 2026-10-07 (RAG-01): a versao anterior usava
    `max(cosseno, sigmoid)`. Um caso fora de escopo com cosseno 0.753 e
    sigmoid 0.0067 (o reranker dizendo "irrelevante") era ADMITIDO pelo
    cosseno - e o gate de avaliacao, que mede so o sigmoid, reportava
    rejeicao. Os grupos de cosseno in-scope e out-of-scope se sobrepoem
    (ver scripts/eval_rag.py); o sigmoid separa (in-scope >= 0.975,
    out-of-scope <= 0.050 no dataset de avaliacao). O caso que motivou o
    `max` (BM25 forte, cosseno baixo) continua coberto: o reranker avalia o
    par query+chunk e admite se for relevante.
    """
    calibrado = hit.get("rerank_score_calibrated")
    if calibrado is None and hit.get("rerank_score") is not None:
        # logit cru sem a calibracao (chamador que nao passou por rerank()):
        # aplica a mesma sigmoid de rerank(), nunca um clamp do logit.
        calibrado = _sigmoid_calibrate(float(hit["rerank_score"]))
    if calibrado is not None:
        return float(calibrado)
    return float(hit["score"])


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
    # A reference_library nao e curada por incidente (manuais tecnicos
    # genericos) - qualquer query relacionada a SAP tende a achar ALGO
    # semanticamente proximo nela, mesmo quando o incidente reportado nao
    # tem relacao real com nenhum documento conhecido (caso out-of-scope).
    # Por isso o fallback exige um score mais alto que o de incidents
    # (documentos feitos sob medida).
    #
    # RECALIBRADO 2026-10-01 contra o corpus REAL (medido, nao estimado).
    # O valor anterior era 0.85, justificado por um comentario que citava
    # "766k+ chunks" - corpus que nunca existiu aqui. O proprio codigo
    # registrava que o limiar estava por recalibrar (DA-44 "Limitacoes",
    # item 2).
    #
    # Medicao (embeddings `nomic-embed-text` via Ollama, 552 docs /
    # 100.805 chunks em `sap_reference_library`):
    #
    #   - 20 queries VERDADEIRAS (a resposta existe no acervo): score do
    #     top-1 min=0.672, p50=0.762, max=0.886
    #   - 15 queries FALSAS (sem relacao com integracao SAP): max=0.658
    #
    # Os intervalos NAO se sobrepoem: max dos falsos 0.658 < min dos
    # verdadeiros 0.672. Um threshold no meio da faixa (0.665) separa os
    # dois grupos com folga dos dois lados.
    #
    # O 0.85 rejeitava 20 de 20 verdadeiros e aceitava 0 de 15 falsos.
    # Isso nao e' um limiar conservador demais, e' um limiar que DESLIGAVA
    # o fallback inteiro: a reference_library era 21 GB indexados para
    # nunca entrar em resposta nenhuma. Medido no caminho real do produto
    # (nao na busca direta, onde o default e' 0.5), 1 de 12 consultas de
    # diagnostico disparava o fallback.
    #
    # CUIDADO ao recalibrar de novo: os numeros acima valem para
    # `nomic-embed-text` neste corpus. Trocar o modelo de embedding
    # desloca a escala de cosseno e invalidaria esta medicao - o mesmo
    # motivo de a DA-45 existir. Com corpus maior (o ingest estava em 26%
    # quando medido), reavalie: mais documentos quase synonymous empurram
    # o max dos falsos para cima, e 0.665 pode voltar a vazar.
    # Validacao 2026-10-07 (M-23): este comentario citava
    # scripts/calibrate_reference_fallback.py, que NAO existe no repositorio -
    # a medicao acima nao e reproduzivel daqui. Ao recalibrar, registre o
    # indice medido (data/index_manifest.json, gerado pelo ingest: pontos,
    # identidade do embedding, data) junto com o novo valor.

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
        except (ValueError, RuntimeError, UnexpectedResponse) as _ref_err:
            # Distingue falha de infra (Qdrant inacessivel) de colecao vazia:
            # sem log, o diagnóstico parece correto quando na verdade o retrieval falhou.
            # UnexpectedResponse cobre o 404 do Qdrant quando a collection
            # sap_reference_library nao existe (ambiente novo, biblioteca
            # nao indexada) - antes escapava do except e derrubava o
            # /diagnose com 500 em vez de degradar sem o fallback.
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
