"""Wrapper de ingestao para o CI/scripts externos.

Traduz a interface --source/--collection (usada no workflow de CI)
para a interface real de app.rag.ingest (--target).

Uso (CI):
    uv run python -m scripts.ingest --source data/sample_docs --collection sap_docs

Equivale a:
    uv run python -m app.rag.ingest --target incidents

Mapeamento de --collection:
    sap_docs / sap_incident_docs  -> --target incidents
    sap_reference_library         -> --target reference
    all                           -> --target all
"""

import argparse
import subprocess
import sys

_COLLECTION_MAP: dict[str, str] = {
    "sap_docs": "incidents",
    "sap_incident_docs": "incidents",
    "incidents": "incidents",
    "sap_reference_library": "reference",
    "reference": "reference",
    "all": "all",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingestao RAG (wrapper CI)")
    parser.add_argument(
        "--source",
        default=None,
        help="Diretorio de documentos (ignorado — app.rag.ingest usa paths internos configurados).",
    )
    parser.add_argument(
        "--collection",
        default="sap_docs",
        help="Collection alvo: sap_docs | sap_incident_docs | sap_reference_library | all",
    )
    parser.add_argument("--reset-collection", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    target = _COLLECTION_MAP.get(args.collection)
    if target is None:
        print(
            f"[scripts.ingest] collection desconhecida: {args.collection!r}. "
            f"Opcoes: {sorted(_COLLECTION_MAP)}"
        )
        return 1

    cmd = [sys.executable, "-m", "app.rag.ingest", "--target", target]
    if args.reset_collection:
        cmd.append("--reset-collection")
    if args.limit is not None:
        cmd.extend(["--limit", str(args.limit)])

    print(f"[scripts.ingest] -> {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
