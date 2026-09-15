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
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pymupdf4llm
from fastembed import SparseTextEmbedding
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
    state_file.write_text(
        json.dumps(sorted(processed), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def extract_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def extract_pages_with_metadata(path: Path) -> list[dict]:
    md_pages = pymupdf4llm.to_markdown(str(path), page_chunks=True)
    result = []
    for page in md_pages:
        t = page.get("text", "").strip()
        if t:
            result.append(
                {
                    "text": t,
                    "page_number": page.get("metadata", {}).get("page", 0) + 1,
                }
            )
    return result


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(65536))
    return h.hexdigest()[:16]


def infer_category(filename: str) -> str:
    name = filename.lower()
    if any(k in name for k in ["security", "authorization", "auth", "xsuaa"]):
        return "security"
    if any(k in name for k in ["abap", "rap", "bapi", "rfc"]):
        return "abap"
    if any(k in name for k in ["integration", "cpi", "iflow", "idoc", "odata", "api"]):
        return "integration"
    if any(k in name for k in ["cap", "btp", "cloud"]):
        return "cap_btp"
    if any(k in name for k in ["fiori", "ui5", "frontend"]):
        return "ui"
    if any(k in name for k in ["hana", "sql", "database", "db"]):
        return "database"
    if any(k in name for k in ["successfactor", "hcm", "hr", "payroll"]):
        return "hcm"
    if any(k in name for k in ["finance", "fi", "co", "accounting"]):
        return "finance"
    if any(k in name for k in ["mm", "material", "procurement", "ariba", "vendor"]):
        return "procurement"
    if any(k in name for k in ["sd", "sales", "order", "crm"]):
        return "sales"
    return "general"


def probe_vector_size(embeddings: OllamaEmbeddings) -> int:
    return len(embeddings.embed_query("probe"))


def ensure_collection(
    client: QdrantClient, collection_name: str, vector_size: int, hybrid: bool
) -> None:
    """Cria a collection se nao existir. Se existir com schema
    incompativel (ex: vetor unico antigo, sem suporte a hybrid),
    recria do zero - aceitavel para o volume de dados deste projeto."""
    existing = [c.name for c in client.get_collections().collections]

    needs_recreate = False
    if collection_name in existing:
        info = client.get_collection(collection_name)
        has_named_dense = bool(info.config.params.vectors) and "dense" in (
            info.config.params.vectors or {}
        )
        if hybrid and not has_named_dense:
            needs_recreate = True
        if needs_recreate:
            print(
                f"Collection '{collection_name}' tem schema antigo (incompativel com hybrid) - recriando."
            )
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


def embed_and_upsert(
    client,
    collection,
    embeddings,
    chunks: list[dict],
    source: str,
    hybrid: bool,
    doc_meta: dict | None = None,
) -> None:
    delete_existing_points_for_source(client, collection, source)
    doc_meta = doc_meta or {}
    ingested_at = datetime.now(UTC).isoformat()

    for i in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[i : i + EMBED_BATCH_SIZE]
        texts = [c["text"] for c in batch]
        dense_vectors = embeddings.embed_documents(texts)

        points = []
        for j, (chunk_data, dense_vec) in enumerate(zip(batch, dense_vectors)):
            chunk_text = chunk_data["text"]
            if hybrid:
                vector = {"dense": dense_vec, "sparse": _sparse_vector_for(chunk_text)}
            else:
                vector = dense_vec
            payload = {
                "source": source,
                "text": chunk_text,
                "filename": doc_meta.get("filename", source),
                "page_number": chunk_data.get("page_number"),
                "document_id": doc_meta.get("document_id", str(uuid4())),
                "chunk_index": i + j,
                "file_hash": doc_meta.get("file_hash", ""),
                "title": doc_meta.get("title", Path(source).stem),
                "category": doc_meta.get("category", "general"),
                "ingested_at": ingested_at,
            }
            points.append(PointStruct(id=str(uuid4()), vector=vector, payload=payload))

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
    splitter = MarkdownTextSplitter(
        chunk_size=cfg["chunk_size"], chunk_overlap=cfg["chunk_overlap"]
    )
    client = QdrantClient(url=QDRANT_URL)

    vector_size = probe_vector_size(embeddings)
    ensure_collection(client, cfg["collection"], vector_size, cfg["hybrid"])

    for idx, path in enumerate(pending, start=1):
        rel = path.relative_to(source_dir)
        print(f"[{target}] ({idx}/{len(pending)}) processando: {rel}")
        try:
            filename = path.name
            fhash = file_hash(path)
            category = infer_category(filename)
            document_id = str(uuid4())
            doc_meta = {
                "filename": filename,
                "file_hash": fhash,
                "category": category,
                "document_id": document_id,
                "title": path.stem,
            }

            if path.suffix.lower() == ".pdf":
                pages = extract_pages_with_metadata(path)
                if not pages:
                    print("    [aviso] nenhum texto extraido do PDF, pulando")
                    processed.add(str(path))
                    save_state(cfg["state_file"], processed)
                    continue
                chunks = []
                for page in pages:
                    for chunk_text in splitter.split_text(page["text"]):
                        chunks.append({"text": chunk_text, "page_number": page["page_number"]})
            else:
                chunks = [
                    {"text": c, "page_number": None}
                    for c in splitter.split_text(extract_text(path))
                ]

            if not chunks:
                print("    [aviso] nenhum chunk gerado, pulando")
            else:
                embed_and_upsert(
                    client,
                    cfg["collection"],
                    embeddings,
                    chunks,
                    str(rel),
                    cfg["hybrid"],
                    doc_meta,
                )
                print(f"    {len(chunks)} chunk(s) indexados (categoria: {category})")
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
