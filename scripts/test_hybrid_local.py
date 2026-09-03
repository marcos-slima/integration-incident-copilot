#!/usr/bin/env python3
"""Teste local do Hybrid Retriever, SEM Docker/servidor Qdrant.

Usa QdrantClient(":memory:") - modo embutido do proprio cliente
Python, roda inteiramente em processo, sem precisar de servico
nenhum no ar. Serve para validar a MECANICA (schema dense+sparse,
ingest, fusao RRF) antes de depender da stack Docker completa.

Nao substitui o teste real contra o Qdrant de producao (~/ai-stack)
- e um teste de fumaca rapido, isolado, para debugar a logica sem
depender de infraestrutura externa.

Uso:
    uv run python scripts/test_hybrid_local.py
"""

import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from fastembed import SparseTextEmbedding
from langchain_ollama import OllamaEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    Fusion,
    FusionQuery,
    PointStruct,
    Prefetch,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from app.config import settings

COLLECTION = "test_hybrid_local"

DOCS = [
    {
        "source": "cpi_http_401.md",
        "text": "CPI iFlow Erro HTTP 401 Unauthorized. Credencial armazenada no Security Material expirada ou incorreta. Token OAuth2 expirado.",
    },
    {
        "source": "rfc_connection_refused.md",
        "text": "RFC Destination Connection Refused. Erro RFC_COMMUNICATION_FAILURE. Servico RFC gateway do sistema de destino nao esta ativo.",
    },
    {
        "source": "idoc_status_51.md",
        "text": "IDoc Status 51 Application Document Not Posted. Material 4711 nao cadastrado no centro 1000.",
    },
]


def cosine_similarity(a, b) -> float:
    a_arr, b_arr = np.array(a), np.array(b)
    denom = np.linalg.norm(a_arr) * np.linalg.norm(b_arr)
    return float(np.dot(a_arr, b_arr) / denom) if denom else 0.0


def main() -> None:
    print("=== Teste local do Hybrid Retriever (Qdrant :memory:, sem Docker) ===\n")

    print("1. Conectando embeddings (Ollama continua sendo necessario - roda local, nao e Docker)")
    dense_embeddings = OllamaEmbeddings(model=settings.embedding_model)
    sparse_model = SparseTextEmbedding(model_name="Qdrant/bm25")

    print("2. Criando cliente Qdrant EM MEMORIA (sem servidor, sem Docker)")
    client = QdrantClient(":memory:")

    print("3. Descobrindo dimensao do vetor denso")
    vector_size = len(dense_embeddings.embed_query("probe"))

    print("4. Criando collection hybrid (dense + sparse nomeados)")
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config={"dense": VectorParams(size=vector_size, distance=Distance.COSINE)},
        sparse_vectors_config={"sparse": SparseVectorParams()},
    )

    print("5. Indexando 3 documentos de exemplo (dense + sparse)")
    for doc in DOCS:
        dense_vec = dense_embeddings.embed_query(doc["text"])
        sparse_emb = next(sparse_model.embed([doc["text"]]))
        sparse_vec = SparseVector(
            indices=sparse_emb.indices.tolist(), values=sparse_emb.values.tolist()
        )
        client.upsert(
            collection_name=COLLECTION,
            points=[
                PointStruct(
                    id=str(uuid4()),
                    vector={"dense": dense_vec, "sparse": sparse_vec},
                    payload={"source": doc["source"], "text": doc["text"]},
                )
            ],
        )
    print(f"   {len(DOCS)} documentos indexados.\n")

    def hybrid_query(query: str):
        dense_q = dense_embeddings.embed_query(query)
        sparse_emb = next(sparse_model.embed([query]))
        sparse_q = SparseVector(
            indices=sparse_emb.indices.tolist(), values=sparse_emb.values.tolist()
        )

        fused = client.query_points(
            collection_name=COLLECTION,
            prefetch=[
                Prefetch(query=dense_q, using="dense", limit=10),
                Prefetch(query=sparse_q, using="sparse", limit=10),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=3,
            with_vectors=["dense"],
        ).points

        print(f"   Query: {query!r}")
        for hit in fused:
            stored_dense = hit.vector["dense"] if isinstance(hit.vector, dict) else hit.vector
            cos = cosine_similarity(dense_q, stored_dense)
            print(
                f"     -> {hit.payload['source']}  (rrf_rank_score={hit.score:.4f}, cosine_denso={cos:.4f})"
            )
        print()

    print(
        "6. Testando queries (termo exato de erro, deveria favorecer o doc certo via sparse/BM25):\n"
    )
    hybrid_query("RFC_COMMUNICATION_FAILURE")
    hybrid_query("erro 401 credencial expirada")
    hybrid_query("material nao cadastrado centro")

    print("=== Teste concluido. Se cada query trouxe o documento certo em 1o lugar, ===")
    print("=== a MECANICA do hybrid retriever esta correta. Isso NAO substitui   ===")
    print("=== rodar contra a stack real (~/ai-stack) + a suite pytest completa. ===")


if __name__ == "__main__":
    main()
