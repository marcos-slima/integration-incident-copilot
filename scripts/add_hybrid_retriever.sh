#!/usr/bin/env bash
# ============================================================
# Implementa Hybrid Retriever: busca densa (embeddings) + esparsa
# (BM25) combinadas via fusao nativa do Qdrant (RRF).
#
# Decisao de design: a fusao RRF seleciona o melhor candidato, mas
# o score reportado (usado por score_threshold, guardrails e nos
# testes existentes) continua sendo a similaridade de cosseno densa
# pura - preserva a semantica ja validada, ganha o beneficio de
# recall em correspondencia exata de termos (codigos de erro, nomes
# de transacao) que embeddings puros as vezes perdem.
#
# Requer recriar a collection sap_incident_docs (schema muda de
# vetor unico para dense+sparse nomeados) - aceitavel, sao poucos
# documentos, reindexados em segundos.
#
# Uso: rodar dentro de ~/sap-integration-copilot
#   bash add_hybrid_retriever.sh
# ============================================================
set -e

if [ ! -f pyproject.toml ]; then
  echo "ERRO: rode este script dentro de ~/sap-integration-copilot"
  exit 1
fi

echo "=== 1/4 - Adicionando fastembed (BM25 esparso, roda 100% local) ==="
uv add fastembed numpy

echo "=== 2/4 - Reescrevendo app/rag/ingest.py (dense + sparse) ==="
cat > app/rag/ingest.py << 'INGESTEOF'
"""Ingestao de documentos no Qdrant, com duas fontes/collections
separadas (incidents / reference).

A collection 'incidents' agora indexa vetores DENSOS (embeddings
semanticos, via Ollama) e ESPARSOS (BM25, via fastembed) lado a lado,
na mesma collection, como campos nomeados - habilita hybrid search
(retriever.py faz a fusao). A collection 'reference' continua so
densa (nao participa do fluxo de diagnostico, nao precisa da mesma
sofisticacao).

Uso:
    uv run python -m app.rag.ingest --target incidents
    uv run python -m app.rag.ingest --target reference
"""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from fastembed import SparseTextEmbedding
from langchain_community.document_loaders import PyPDFLoader
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import MarkdownTextSplitter
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from app.config import settings

BASE_DIR = Path(__file__).resolve().parents[2]
EMBEDDING_MODEL = settings.embedding_model
SPARSE_MODEL_NAME = "Qdrant/bm25"  # BM25 classico, sem rede neural - roda so em CPU, sem GPU
QDRANT_URL = settings.qdrant_url
EMBED_BATCH_SIZE = 16

TARGETS = {
    "incidents": {
        "collection": "sap_incident_docs",
        "primary_dir": BASE_DIR / "data" / "knowledge_base",
        "fallback_dir": BASE_DIR / "data" / "sample_docs",
        "chunk_size": 500,
        "chunk_overlap": 50,
        "state_file": BASE_DIR / "data" / ".ingest_state_incidents.json",
        "hybrid": True,
    },
    "reference": {
        "collection": "sap_reference_library",
        "primary_dir": BASE_DIR / "data" / "reference_library",
        "fallback_dir": None,
        "chunk_size": 1000,
        "chunk_overlap": 150,
        "state_file": BASE_DIR / "data" / ".ingest_state_reference.json",
        "hybrid": False,
    },
}

_sparse_model: SparseTextEmbedding | None = None


def _get_sparse_model() -> SparseTextEmbedding:
    global _sparse_model
    if _sparse_model is None:
        _sparse_model = SparseTextEmbedding(model_name=SPARSE_MODEL_NAME)
    return _sparse_model


def resolve_source_dir(cfg: dict) -> Path:
    primary = cfg["primary_dir"]
    if primary.exists() and any(primary.rglob("*")):
        return primary
    if cfg["fallback_dir"] is not None:
        print(f"[aviso] {primary} vazia, usando fallback {cfg['fallback_dir']}")
        return cfg["fallback_dir"]
    return primary


def find_files(source_dir: Path, excludes: list[str]) -> list[Path]:
    files = list(source_dir.rglob("*.md")) + list(source_dir.rglob("*.pdf"))
    if excludes:
        files = [f for f in files if not any(ex.lower() in str(f).lower() for ex in excludes)]
    return sorted(files)


def load_state(state_file: Path) -> set[str]:
    if state_file.exists():
        return set(json.loads(state_file.read_text(encoding="utf-8")))
    return set()


def save_state(state_file: Path, processed: set[str]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(sorted(processed), ensure_ascii=False, indent=2), encoding="utf-8")


def extract_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        pages = PyPDFLoader(str(path)).load()
        return "\n\n".join(p.page_content for p in pages)
    return path.read_text(encoding="utf-8", errors="ignore")


def probe_vector_size(embeddings: OllamaEmbeddings) -> int:
    return len(embeddings.embed_query("probe"))


def ensure_collection(client: QdrantClient, collection_name: str, vector_size: int, hybrid: bool) -> None:
    """Cria a collection se nao existir. Se existir com schema
    incompativel (ex: vetor unico antigo, sem suporte a hybrid),
    recria do zero - aceitavel para o volume de dados deste projeto."""
    existing = [c.name for c in client.get_collections().collections]

    needs_recreate = False
    if collection_name in existing:
        info = client.get_collection(collection_name)
        has_named_dense = bool(info.config.params.vectors) and "dense" in (info.config.params.vectors or {})
        if hybrid and not has_named_dense:
            needs_recreate = True
        if needs_recreate:
            print(f"Collection '{collection_name}' tem schema antigo (incompativel com hybrid) - recriando.")
            client.delete_collection(collection_name)
            existing.remove(collection_name)

    if collection_name not in existing:
        if hybrid:
            client.create_collection(
                collection_name=collection_name,
                vectors_config={"dense": VectorParams(size=vector_size, distance=Distance.COSINE)},
                sparse_vectors_config={"sparse": SparseVectorParams()},
            )
        else:
            client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )
        print(f"Collection '{collection_name}' criada ({'hybrid' if hybrid else 'dense-only'}).")


def delete_existing_points_for_source(client: QdrantClient, collection: str, source: str) -> None:
    client.delete(
        collection_name=collection,
        points_selector=Filter(must=[FieldCondition(key="source", match=MatchValue(value=source))]),
    )


def _sparse_vector_for(text: str) -> SparseVector:
    embedding = next(_get_sparse_model().embed([text]))
    return SparseVector(indices=embedding.indices.tolist(), values=embedding.values.tolist())


def embed_and_upsert(client, collection, embeddings, chunks: list[str], source: str, hybrid: bool) -> None:
    delete_existing_points_for_source(client, collection, source)

    for i in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[i : i + EMBED_BATCH_SIZE]
        dense_vectors = embeddings.embed_documents(batch)

        points = []
        for text, dense_vec in zip(batch, dense_vectors):
            if hybrid:
                vector = {"dense": dense_vec, "sparse": _sparse_vector_for(text)}
            else:
                vector = dense_vec
            points.append(PointStruct(id=str(uuid4()), vector=vector, payload={"source": source, "text": text}))

        client.upsert(collection_name=collection, points=points)


def run_ingest(target: str, limit: int | None, excludes: list[str], reset: bool) -> None:
    cfg = TARGETS[target]
    source_dir = resolve_source_dir(cfg)
    print(f"[{target}] Fonte: {source_dir}")

    all_files = find_files(source_dir, excludes)
    print(f"[{target}] {len(all_files)} arquivo(s) encontrados (.md + .pdf, recursivo)")

    processed = set() if reset else load_state(cfg["state_file"])
    pending = [f for f in all_files if str(f) not in processed]
    print(f"[{target}] {len(processed)} ja processados anteriormente, {len(pending)} pendentes")

    if limit:
        pending = pending[:limit]
        print(f"[{target}] --limit aplicado: processando {len(pending)} arquivo(s) nesta execucao")

    if not pending:
        print(f"[{target}] Nada a fazer.")
        return

    embeddings = OllamaEmbeddings(model=EMBEDDING_MODEL)
    splitter = MarkdownTextSplitter(chunk_size=cfg["chunk_size"], chunk_overlap=cfg["chunk_overlap"])
    client = QdrantClient(url=QDRANT_URL)

    vector_size = probe_vector_size(embeddings)
    ensure_collection(client, cfg["collection"], vector_size, cfg["hybrid"])

    for idx, path in enumerate(pending, start=1):
        rel = path.relative_to(source_dir)
        print(f"[{target}] ({idx}/{len(pending)}) processando: {rel}")
        try:
            text = extract_text(path)
            chunks = splitter.split_text(text)
            if not chunks:
                print("    [aviso] nenhum texto extraido, pulando")
            else:
                embed_and_upsert(client, cfg["collection"], embeddings, chunks, str(rel), cfg["hybrid"])
                print(f"    {len(chunks)} chunk(s) indexados")
        except Exception as exc:  # noqa: BLE001
            print(f"    [ERRO] falhou em {rel}: {exc} -- pulando este arquivo")
            continue

        processed.add(str(path))
        save_state(cfg["state_file"], processed)

    print(f"[{target}] Concluido. Total processado ate agora: {len(processed)} arquivo(s).")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["incidents", "reference", "all"], default="incidents")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    targets = ["incidents", "reference"] if args.target == "all" else [args.target]
    for t in targets:
        run_ingest(t, args.limit, args.exclude, args.reset)


if __name__ == "__main__":
    main()
INGESTEOF

echo "=== 3/4 - Reescrevendo app/rag/retriever.py (fusao hybrid RRF) ==="
cat > app/rag/retriever.py << 'RETRIEVEREOF'
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

from functools import lru_cache

import numpy as np
from fastembed import SparseTextEmbedding
from langchain_ollama import OllamaEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.models import Fusion, FusionQuery, Prefetch, SparseVector

from app.config import settings

EMBEDDING_MODEL = settings.embedding_model
SPARSE_MODEL_NAME = "Qdrant/bm25"
QDRANT_URL = settings.qdrant_url

DEFAULT_SCORE_THRESHOLD = 0.5
HYBRID_PREFETCH_LIMIT = 20  # candidatos por perna (dense/sparse) antes da fusao

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
    return OllamaEmbeddings(model=EMBEDDING_MODEL)


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


def _retrieve_hybrid(query: str, collection_name: str, top_k: int, score_threshold: float) -> list[dict]:
    client = _get_qdrant_client()
    dense_query = _get_embeddings().embed_query(query)
    sparse_query = _sparse_query_vector(query)

    fused = client.query_points(
        collection_name=collection_name,
        prefetch=[
            Prefetch(query=dense_query, using="dense", limit=HYBRID_PREFETCH_LIMIT),
            Prefetch(query=sparse_query, using="sparse", limit=HYBRID_PREFETCH_LIMIT),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=top_k,
        with_vectors=["dense"],
    ).points

    results = []
    for hit in fused:
        stored_dense = hit.vector["dense"] if isinstance(hit.vector, dict) else hit.vector
        cosine_score = _cosine_similarity(dense_query, stored_dense)
        if cosine_score < score_threshold:
            continue
        results.append(
            {
                "source": hit.payload.get("source"),
                "text": hit.payload.get("text"),
                "score": cosine_score,
            }
        )
    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def _retrieve_dense_only(query: str, collection_name: str, top_k: int, score_threshold: float) -> list[dict]:
    client = _get_qdrant_client()
    query_vector = _get_embeddings().embed_query(query)
    results = client.query_points(
        collection_name=collection_name,
        query=query_vector,
        limit=top_k,
        score_threshold=score_threshold,
    ).points
    return [
        {"source": hit.payload.get("source"), "text": hit.payload.get("text"), "score": hit.score}
        for hit in results
    ]


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
        return _retrieve_hybrid(query, collection_name, top_k, score_threshold)
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
RETRIEVEREOF

echo "=== 4/4 - Sync + lint ==="
uv sync
uv run ruff check --fix app/rag/ingest.py app/rag/retriever.py
uv run ruff format app/rag/ingest.py app/rag/retriever.py

echo
echo "============================================================"
echo "Hybrid Retriever implementado. PASSOS OBRIGATORIOS antes de"
echo "considerar isso pronto:"
echo
echo "1. Reindexar (schema da collection mudou, precisa recriar):"
echo "   uv run python -m app.rag.ingest --target incidents --reset"
echo
echo "2. Teste isolado rapido:"
echo "   uv run python -m app.rag.retriever incidents \"RFC_COMMUNICATION_FAILURE\""
echo
echo "3. Suite completa - CRITICO, essa mudanca troca o mecanismo"
echo "   de busca inteiro, precisa validar que nada regrediu:"
echo "   uv run pytest -v"
echo "============================================================"
