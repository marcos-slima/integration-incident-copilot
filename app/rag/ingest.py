"""Ingestao de documentos no Qdrant, com duas fontes/collections
separadas (incidents / reference).

A collection 'incidents' agora indexa vetores DENSOS (embeddings
semanticos, via Ollama) e ESPARSOS (BM25, via fastembed) lado a lado,
na mesma collection, como campos nomeados - habilita hybrid search
(A.py faz a fusao). A collection 'reference' continua so
densa (nao participa do fluxo de diagnostico, nao precisa da mesma
sofisticacao).

Uso:
    uv run python -m app.rag.ingest --target incidents
    uv run python -m app.rag.ingest --target reference
    uv run python -m app.rag.ingest --target incidents --reset-state
    uv run python -m app.rag.ingest --target incidents --reset-collection
"""

import argparse
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pymupdf4llm
from fastembed import SparseTextEmbedding
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import MarkdownTextSplitter
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from app.config import settings

# Desativa o motor de layout via ONNX (pymupdf.layout / BoxRFDGNN) - crasha
# o processo (SIGSEGV, sem traceback Python) em determinados PDFs/EPUBs,
# independente de threading (reproduzido isolado, single-thread, com
# PYTHONFAULTHANDLER=1). Volta ao parser heuristico legado do pymupdf4llm
# (sem ML), estavel para o volume e diversidade de arquivos deste projeto.
pymupdf4llm.use_layout(False)

BASE_DIR = Path(__file__).resolve().parents[2]
EMBEDDING_MODEL = settings.embedding_model
SPARSE_MODEL_NAME = "Qdrant/bm25"  # BM25 classico, sem rede neural - roda so em CPU, sem GPU
QDRANT_URL = settings.qdrant_url
EMBED_BATCH_SIZE = 16
MAX_WORKERS = 4  # alinhado com OLLAMA_NUM_PARALLEL=4
SUPPORTED_SUFFIXES = {".md", ".pdf", ".epub"}

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
_sparse_model_lock = threading.Lock()

# pymupdf4llm/PyMuPDF usa estado global nativo (MuPDF) para inferencia de
# layout (modelo ONNX de deteccao de estrutura de pagina) - chamar
# to_markdown() de threads diferentes ao mesmo tempo segfaulta o processo
# (visto com PYTHONFAULTHANDLER=1: crash dentro de page.get_layout(), com
# varias threads simultaneas na mesma pilha nativa). Serializa so a
# extracao (CPU-bound, nativa); embedding/upsert (I/O) continuam paralelos.
_pdf_extract_lock = threading.Lock()


def _get_sparse_model() -> SparseTextEmbedding:
    global _sparse_model
    # A inicializacao pode disparar download/carregamento do modelo. Protege
    # contra duas threads fazendo isso simultaneamente.
    with _sparse_model_lock:
        if _sparse_model is None:
            _sparse_model = SparseTextEmbedding(model_name=SPARSE_MODEL_NAME)
    return _sparse_model


def resolve_source_dir(cfg: dict) -> Path:
    primary = cfg["primary_dir"]
    if primary.exists() and any(
        path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES for path in primary.rglob("*")
    ):
        return primary
    if cfg["fallback_dir"] is not None:
        print(f"[aviso] {primary} vazia, usando fallback {cfg['fallback_dir']}")
        return cfg["fallback_dir"]
    return primary


def find_files(source_dir: Path, excludes: list[str]) -> list[Path]:
    if not source_dir.exists():
        return []
    files = [
        path
        for path in source_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    ]
    if excludes:
        files = [f for f in files if not any(ex.lower() in str(f).lower() for ex in excludes)]
    return sorted(files)


def load_state(state_file: Path) -> dict[str, str]:
    """Carrega estado de ingestao.
    Formato novo: {hash_key: filepath} — detecta mudancas por conteudo.
    Formato legado: lista de paths — convertida automaticamente.
    """
    if not state_file.exists():
        return {}
    data = json.loads(state_file.read_text(encoding="utf-8"))
    if isinstance(data, list):
        # Converte formato legado (lista de paths) para dict
        return {p: p for p in data}
    return data


def _state_key(path: Path) -> str:
    """Chave de estado: hash:filename — detecta mudancas mesmo com renomeacao."""
    return f"{file_hash(path)}:{path.name}"


def save_state(state_file: Path, processed: dict[str, str]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(processed, ensure_ascii=False, indent=2), encoding="utf-8")


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
    """Retorna o SHA-256 de todo o arquivo sem carrega-lo integralmente em memoria."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


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
    client: QdrantClient,
    collection_name: str,
    vector_size: int,
    hybrid: bool,
    allow_recreate: bool = False,
) -> None:
    """Cria a collection se nao existir. Se existir com schema
    incompativel (ex: vetor unico antigo, sem suporte a hybrid),
    recria do zero - aceitavel para o volume de dados deste projeto."""
    existing = [c.name for c in client.get_collections().collections]

    needs_recreate = False
    if collection_name in existing:
        info = client.get_collection(collection_name)
        vectors = info.config.params.vectors
        sparse_vectors = getattr(info.config.params, "sparse_vectors", None)
        has_named_dense = isinstance(vectors, dict) and "dense" in vectors
        has_named_sparse = isinstance(sparse_vectors, dict) and "sparse" in sparse_vectors
        has_unnamed_dense = vectors is not None and not isinstance(vectors, dict)
        dense_config = vectors.get("dense") if has_named_dense else vectors
        has_expected_size = getattr(dense_config, "size", None) == vector_size

        if (
            hybrid
            and not (has_named_dense and has_named_sparse and has_expected_size)
            or not hybrid
            and not (has_unnamed_dense and not sparse_vectors and has_expected_size)
        ):
            needs_recreate = True
        if needs_recreate:
            expected_schema = "hybrid (dense + sparse)" if hybrid else "dense-only"
            if not allow_recreate:
                raise RuntimeError(
                    f"Collection '{collection_name}' tem schema incompativel com {expected_schema}. "
                    f"Execute novamente com --reset-collection para recriar do zero "
                    f"(atenção: todos os dados indexados serão perdidos)."
                )
            print(
                f"Collection '{collection_name}' tem schema incompativel com {expected_schema} - recriando (--reset-collection ativo)."
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
        # Payload indexes para campos usados em filtros — melhora performance
        # à medida que a collection cresce (Qdrant docs: payload indexes).
        for field_name, field_schema in [
            ("source", "keyword"),
            ("document_id", "keyword"),
            ("category", "keyword"),
            ("file_hash", "keyword"),
        ]:
            try:
                client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=field_schema,
                )
            except Exception:  # noqa: BLE001 S110
                pass  # índice já existe ou versão do Qdrant não suporta — não crítico


def deterministic_point_id(document_id: str, chunk_index: int) -> str:
    """UUID estavel e aceito pelo Qdrant para um chunk do documento -
    ver docstring de `deterministic_document_id` para por que isso
    basta para upsert idempotente sem delete previo."""
    return str(uuid5(NAMESPACE_URL, f"{document_id}::chunk::{chunk_index}"))


def deterministic_document_id(source: str) -> str:
    """UUID estavel para o caminho relativo do arquivo (`source`) -
    DELIBERADAMENTE nao depende do conteudo/hash do arquivo, ao
    contrario de uma versao anterior. Isso e o que torna o upsert
    idempotente sem delete-before-upsert (ver docs/ARCHITECTURE.md,
    secao "Ingestao"): reprocessar o MESMO arquivo (path) sempre gera
    os mesmos `document_id`/point ids por indice de chunk, entao um
    `client.upsert()` novo sobrescreve os chunks existentes no lugar -
    sem janela de indisponibilidade (delete + upsert deixaria a
    collection momentaneamente sem esses pontos) e sem duplicar dados
    quando so o CONTEUDO do arquivo muda (mesmo indice de chunk == mesmo
    point id == overwrite, nao um ponto novo orfao).

    Trade-off aceito: se um arquivo encolhe (produz MENOS chunks que a
    versao anterior), os indices de chunk que deixaram de existir nao
    sao removidos automaticamente (nao ha "delete dos que sobraram"
    sem reintroduzir a janela de indisponibilidade que este esquema
    evita). Para uma limpeza completa apos edicoes que reduzem
    bastante o numero de chunks de varios arquivos, use
    `--reset-collection` (reindexa tudo do zero)."""
    return str(uuid5(NAMESPACE_URL, f"ingest-document::{source}"))


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
                "document_id": doc_meta["document_id"],
                "chunk_index": i + j,
                "file_hash": doc_meta.get("file_hash", ""),
                "title": doc_meta.get("title", Path(source).stem),
                "category": doc_meta.get("category", "general"),
                "ingested_at": ingested_at,
            }
            point_id = deterministic_point_id(doc_meta["document_id"], i + j)
            points.append(PointStruct(id=point_id, vector=vector, payload=payload))

        client.upsert(collection_name=collection, points=points)


def run_ingest(
    target: str,
    limit: int | None,
    excludes: list[str],
    reset_state: bool,
    reset_collection: bool,
) -> int:
    """Retorna o numero de arquivos que falharam (0 = sucesso total)."""
    cfg = TARGETS[target]
    source_dir = resolve_source_dir(cfg)
    print(f"[{target}] Fonte: {source_dir}")

    all_files = find_files(source_dir, excludes)
    print(f"[{target}] {len(all_files)} arquivo(s) encontrados (.md + .pdf + .epub, recursivo)")

    processed = {} if (reset_state or reset_collection) else load_state(cfg["state_file"])
    if reset_state or reset_collection:
        # Persiste imediatamente para que --reset-state tenha efeito mesmo
        # quando nao ha arquivos elegiveis nesta execucao.
        save_state(cfg["state_file"], processed)
    pending = [f for f in all_files if _state_key(f) not in processed]
    print(f"[{target}] {len(processed)} ja processados anteriormente, {len(pending)} pendentes")

    if limit:
        pending = pending[:limit]
        print(f"[{target}] --limit aplicado: processando {len(pending)} arquivo(s) nesta execucao")

    client = QdrantClient(url=QDRANT_URL)
    if reset_collection:
        existing = {collection.name for collection in client.get_collections().collections}
        if cfg["collection"] in existing:
            client.delete_collection(cfg["collection"])
            print(f"Collection '{cfg['collection']}' removida por --reset-collection.")

    if not pending:
        print(f"[{target}] Nada a fazer.")
        return 0

    embeddings = OllamaEmbeddings(model=EMBEDDING_MODEL)
    splitter = MarkdownTextSplitter(
        chunk_size=cfg["chunk_size"], chunk_overlap=cfg["chunk_overlap"]
    )
    vector_size = probe_vector_size(embeddings)
    ensure_collection(
        client, cfg["collection"], vector_size, cfg["hybrid"], allow_recreate=reset_collection
    )

    # Carrega o BM25 antes do paralelismo; o acesso posterior e somente para
    # gerar vetores, nao para inicializar/downloadar o modelo em varias threads.
    if cfg["hybrid"]:
        _get_sparse_model()

    state_lock = threading.Lock()
    progress_lock = threading.Lock()
    done_count = 0
    error_count = 0

    def process_one(path: Path) -> None:
        nonlocal done_count, error_count
        rel = path.relative_to(source_dir)
        try:
            filename = path.name
            fhash = file_hash(path)
            category = infer_category(filename)
            source = str(rel)
            document_id = deterministic_document_id(source)
            doc_meta = {
                "filename": filename,
                "file_hash": fhash,
                "category": category,
                "document_id": document_id,
                "title": path.stem,
            }

            if path.suffix.lower() in (".pdf", ".epub"):
                with _pdf_extract_lock:
                    pages = extract_pages_with_metadata(path)
                if not pages:
                    with state_lock:
                        processed[_state_key(path)] = str(path)
                        save_state(cfg["state_file"], processed)
                    with progress_lock:
                        done_count += 1
                        print(
                            f"[{target}] ({done_count}/{len(pending)}) {rel} -- [aviso] sem texto no PDF, pulando"
                        )
                    return
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
                with progress_lock:
                    done_count += 1
                    print(
                        f"[{target}] ({done_count}/{len(pending)}) {rel} -- [aviso] nenhum chunk gerado, pulando"
                    )
            else:
                # Sem delete previo - deterministic_document_id(source) faz o
                # upsert sobrescrever os pontos existentes no lugar (ver
                # docstring de deterministic_document_id).
                embed_and_upsert(
                    client,
                    cfg["collection"],
                    embeddings,
                    chunks,
                    source,
                    cfg["hybrid"],
                    doc_meta,
                )
                with progress_lock:
                    done_count += 1
                    print(
                        f"[{target}] ({done_count}/{len(pending)}) {rel} -- {len(chunks)} chunk(s) indexados (categoria: {category})"
                    )
        except Exception as exc:  # noqa: BLE001
            with progress_lock:
                done_count += 1
                error_count += 1
                print(
                    f"[{target}] ({done_count}/{len(pending)}) {rel} -- [ERRO] {exc} -- pulando este arquivo"
                )
            return

        with state_lock:
            processed[_state_key(path)] = str(path)
            save_state(cfg["state_file"], processed)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(process_one, path) for path in pending]
        for fut in as_completed(futures):
            fut.result()  # relanca excecao inesperada (nao deveria ocorrer, ja tratada acima)

    if error_count:
        print(
            f"[{target}] Concluido com {error_count} erro(s). "
            f"Sucesso: {len(processed)} arquivo(s). Falha: {error_count} arquivo(s)."
        )
    else:
        print(f"[{target}] Concluido. Total processado ate agora: {len(processed)} arquivo(s).")
    return error_count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["incidents", "reference", "all"], default="incidents")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument(
        "--reset",
        "--reset-state",
        dest="reset_state",
        action="store_true",
        help="limpa apenas o estado local e reindexa os arquivos; nao apaga a collection",
    )
    parser.add_argument(
        "--reset-collection",
        action="store_true",
        help="apaga e recria a collection do target; tambem limpa o estado local",
    )
    args = parser.parse_args()

    targets = ["incidents", "reference"] if args.target == "all" else [args.target]
    total_errors = 0
    for t in targets:
        total_errors += run_ingest(
            t, args.limit, args.exclude, args.reset_state, args.reset_collection
        )
    if total_errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
