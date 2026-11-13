"""Base de conhecimento fiscal do DF-e Copilot (P3): ingestao e busca.

Colecao propria e hibrida (dense + BM25) no Qdrant, `dfe_kb`, separada das
colecoes do core. Regras do estudo (§8.4) aplicadas aqui:

- metadados obrigatorios por trecho (fonte, autoridade, versao, hash,
  modelo, status, revisao) vindos do manifesto e do frontmatter curado;
- filtros de aplicabilidade sao aplicados ANTES da busca, nas duas pernas
  (densa e esparsa), nunca depois;
- documento `revogado` nunca entra no resultado de diagnostico;
- sem trecho aplicavel, a busca devolve lista vazia (abstencao), nunca
  um trecho qualquer.

Uso local (Qdrant + Ollama vivos, config do core), a partir de packages/dfe:
    PYTHONPATH=src:../.. uv run --project ../.. python -m dfe_copilot.kb ingest [--reset]
    PYTHONPATH=src:../.. uv run --project ../.. python -m dfe_copilot.kb search "duplicidade" --cstat 204
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

COLLECTION = "dfe_kb"
CORPUS_DIR = Path(__file__).resolve().parents[2] / "data" / "corpus"
CHUNK_CHARS = 1200
PREFETCH_LIMIT = 20
_RULE_ID = re.compile(r"\b\d[A-Z]\d{2}-\d{2}\b")
_CSTAT_REJ = re.compile(r"\b(\d{3})\s+(?:Rej|Den)\.?")
# Linha de tabela de cStat do MOC (secao 4.4): "110 Uso Denegado".
_CSTAT_LINE = re.compile(r"(?m)^\s*(\d{3})\s+(?=[A-ZÀ-Ú])")
_CSTAT_TABLE_START = re.compile(
    r"4\.4\.1\. Tabela de Códigos de Resultado de Processamento \(cStat\)\s*\n"
)

DenseFn = Callable[[list[str]], list[list[float]]]
SparseFn = Callable[[str], Any]  # devolve qdrant_client.models.SparseVector


@dataclass
class Chunk:
    text: str
    payload: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Leitura do corpus
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Frontmatter YAML simples (chave: valor, listas inline). Sem PyYAML."""
    if not text.startswith("---\n"):
        return {}, text
    head, _, body = text[4:].partition("\n---\n")
    meta: dict[str, Any] = {}
    for line in head.splitlines():
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        raw = raw.strip()
        if raw.startswith("[") and raw.endswith("]"):
            value: Any = [v.strip().strip('"') for v in raw[1:-1].split(",") if v.strip()]
        elif raw in ("null", ""):
            value = None
        else:
            value = raw.strip('"')
        meta[key.strip()] = value
    return meta, body


def _split(text: str, size: int = CHUNK_CHARS) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for p in paragraphs:
        if current and len(current) + len(p) + 2 > size:
            chunks.append(current)
            current = p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        chunks.append(current)
    return chunks


def _base_payload(meta: dict[str, Any]) -> dict[str, Any]:
    models = meta.get("model")
    return {
        "source_id": meta.get("source_id"),
        "document_type": meta.get("document_type"),
        "document_number": meta.get("document_number"),
        "version": meta.get("version"),
        "authority_class": meta.get("authority_class"),
        "canonical_url": meta.get("canonical_url"),
        "content_sha256": meta.get("content_sha256"),
        "license_scope": meta.get("license_scope", "public"),
        "rule_id": meta.get("rule_id"),
        "cstat": meta.get("cstat"),
        "rule_ids": [meta["rule_id"]] if meta.get("rule_id") else [],
        "cstats": [meta["cstat"]] if meta.get("cstat") else [],
        "model": models if isinstance(models, list) else ([models] if models else ["55"]),
        "jurisdiction": meta.get("jurisdiction", "nacional"),
        "status": meta.get("status", "vigente"),
        "reviewed_by": meta.get("reviewed_by"),
    }


def _pypdf_pages(path: Path) -> list[dict]:
    """Texto por pagina com pypdf (dependencia do core).

    O extrator padrao do core (pymupdf4llm -> markdown) descarta as tabelas
    de regras de validacao das NTs, justamente onde ficam regra e cStat.
    """
    from pypdf import PdfReader

    pages = []
    for n, page in enumerate(PdfReader(str(path)).pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append({"text": text, "page_number": n})
    return pages


def load_corpus(
    corpus_dir: Path = CORPUS_DIR,
    pdf_pages: Callable[[Path], list[dict]] | None = None,
) -> list[Chunk]:
    """Trechos com metadados a partir do manifesto (PDFs) e dos `.md` curados.

    `pdf_pages(path) -> [{"text", "page_number"}]` e' injetavel; o default usa
    pypdf (preserva as tabelas de regras das NTs).
    """
    manifest = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    chunks: list[Chunk] = []

    for doc in manifest["documents"]:
        # `stored=false`: fonte fora do repositorio (tamanho); entra se o
        # arquivo estiver no corpus local e o hash conferir.
        if not doc.get("file"):
            continue
        path = corpus_dir / doc["file"]
        if not doc.get("stored") and not path.exists():
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != doc["content_sha256"]:
            raise ValueError(f"hash divergente no corpus: {doc['file']}")
        if pdf_pages is None:
            pdf_pages = _pypdf_pages
        in_table = False
        for page in pdf_pages(path):
            in_table = in_table or bool(_CSTAT_TABLE_START.search(page["text"]))
            for i, text in enumerate(_split(page["text"])):
                # Marca regra e cStat citados NO trecho (tabela de regras da
                # NT: "2B08-20 ... Obrig. 204 Rej."), para o filtro por cStat
                # alcancar o documento oficial e nao so a regra curada.
                payload = _base_payload(doc) | {
                    "file": doc["file"],
                    "page_number": page.get("page_number"),
                    "chunk_index": i,
                    "rule_ids": sorted(set(_RULE_ID.findall(text))),
                    "cstats": sorted(
                        set(_CSTAT_REJ.findall(text))
                        | (set(_CSTAT_LINE.findall(text)) if in_table else set())
                    ),
                }
                chunks.append(Chunk(text=text, payload=payload))

    for md in sorted(corpus_dir.glob("*.md")):
        raw = md.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        meta["content_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        meta.setdefault("document_type", "regra_curada")
        meta.setdefault("authority_class", "curadoria")
        for i, text in enumerate(_split(body)):
            payload = _base_payload(meta) | {"file": md.name, "page_number": None, "chunk_index": i}
            chunks.append(Chunk(text=text, payload=payload))
    return chunks


def point_id(chunk: Chunk) -> str:
    p = chunk.payload
    return str(
        uuid5(NAMESPACE_URL, f"dfe_kb::{p['file']}::{p.get('page_number')}::{p['chunk_index']}")
    )


# ---------------------------------------------------------------------------
# Qdrant
# ---------------------------------------------------------------------------


def ensure_collection(client, vector_size: int, reset: bool = False) -> None:
    from qdrant_client.models import Distance, SparseVectorParams, VectorParams

    exists = client.collection_exists(COLLECTION)
    if exists and reset:
        client.delete_collection(COLLECTION)
        exists = False
    if not exists:
        client.create_collection(
            COLLECTION,
            vectors_config={"dense": VectorParams(size=vector_size, distance=Distance.COSINE)},
            sparse_vectors_config={"sparse": SparseVectorParams()},
        )


def ingest(
    client,
    dense_fn: DenseFn,
    sparse_fn: SparseFn,
    chunks: Iterable[Chunk],
    reset: bool = False,
    batch: int = 16,
) -> int:
    from qdrant_client.models import PointStruct

    items = list(chunks)
    if not items:
        return 0
    probe = dense_fn([items[0].text])[0]
    ensure_collection(client, len(probe), reset=reset)
    for i in range(0, len(items), batch):
        part = items[i : i + batch]
        vectors = dense_fn([c.text for c in part])
        client.upsert(
            COLLECTION,
            points=[
                PointStruct(
                    id=point_id(c),
                    vector={"dense": v, "sparse": sparse_fn(c.text)},
                    payload={**c.payload, "text": c.text},
                )
                for c, v in zip(part, vectors)
            ],
        )
    return len(items)


def build_filter(model: str = "55", cstat: str | None = None):
    """Filtro de aplicabilidade aplicado nas duas pernas da busca hibrida."""
    from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

    must = [FieldCondition(key="model", match=MatchAny(any=[model]))]
    if cstat:
        must.append(FieldCondition(key="cstats", match=MatchAny(any=[cstat])))
    must_not = [FieldCondition(key="status", match=MatchValue(value="revogado"))]
    return Filter(must=must, must_not=must_not)


def _evidence_id(p: dict[str, Any]) -> str:
    page = f"p{p['page_number']}" if p.get("page_number") is not None else ""
    return f"kb:{p['file']}#{page}c{p['chunk_index']}"


def search(
    client,
    query: str,
    dense_fn: DenseFn,
    sparse_fn: SparseFn,
    model: str = "55",
    cstat: str | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Busca hibrida filtrada (RRF). Lista vazia = sem fonte aplicavel (abster)."""
    from qdrant_client.models import Fusion, FusionQuery, Prefetch

    if not client.collection_exists(COLLECTION):
        return []
    flt = build_filter(model=model, cstat=cstat)
    dense = dense_fn([query])[0]
    hits = client.query_points(
        COLLECTION,
        prefetch=[
            Prefetch(query=dense, using="dense", filter=flt, limit=PREFETCH_LIMIT),
            Prefetch(query=sparse_fn(query), using="sparse", filter=flt, limit=PREFETCH_LIMIT),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=top_k,
    ).points
    return [
        {
            "evidence_id": _evidence_id(h.payload),
            "trust_level": "rule_reference"
            if h.payload.get("authority_class") == "oficial_gov"
            else "retrieved_document",
            "score": h.score,
            **{k: v for k, v in h.payload.items() if k != "text"},
            "text": h.payload["text"],
        }
        for h in hits
    ]


# ---------------------------------------------------------------------------
# CLI (usa a infraestrutura do core: Qdrant e embeddings configurados)
# ---------------------------------------------------------------------------


def _core_backends():
    from app.rag.ingest import _sparse_vector_for
    from app.rag.retriever import _get_embeddings, _get_qdrant_client

    emb = _get_embeddings()
    return _get_qdrant_client(), emb.embed_documents, _sparse_vector_for


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dfe_copilot.kb")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_ing = sub.add_parser("ingest", help="indexa data/corpus na colecao dfe_kb")
    p_ing.add_argument("--reset", action="store_true", help="recria a colecao dfe_kb")
    p_s = sub.add_parser("search", help="busca hibrida filtrada")
    p_s.add_argument("query")
    p_s.add_argument("--cstat")
    p_s.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args(argv)

    client, dense_fn, sparse_fn = _core_backends()
    if args.cmd == "ingest":
        n = ingest(client, dense_fn, sparse_fn, load_corpus(), reset=args.reset)
        print(f"{n} trechos indexados em {COLLECTION}")
        return 0
    for h in search(client, args.query, dense_fn, sparse_fn, cstat=args.cstat, top_k=args.top_k):
        print(f"{h['score']:.4f} {h['evidence_id']} [{h['trust_level']}] {h['text'][:100]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
