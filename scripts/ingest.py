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
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SUPPORTED_SUFFIXES = {".md", ".pdf", ".epub"}

# Mesma resolucao de app.rag.ingest.resolve_source_dir: usa o diretorio
# primario se ele tiver algum arquivo suportado; senao, o fallback.
_SOURCE_DIRS: dict[str, tuple[Path, Path | None]] = {
    "incidents": (
        _REPO_ROOT / "data" / "knowledge_base",
        _REPO_ROOT / "data" / "sample_docs",
    ),
    "reference": (_REPO_ROOT / "data" / "reference_library", None),
}

_COLLECTION_MAP: dict[str, str] = {
    "sap_docs": "incidents",
    "sap_incident_docs": "incidents",
    "incidents": "incidents",
    "sap_reference_library": "reference",
    "reference": "reference",
    "all": "all",
}


def _tem_documentos(diretorio: Path) -> bool:
    return diretorio.exists() and any(
        caminho.is_file() and caminho.suffix.lower() in _SUPPORTED_SUFFIXES
        for caminho in diretorio.rglob("*")
    )


def _resolve_source_dir(target: str) -> Path | None:
    if target not in _SOURCE_DIRS:
        return None
    primario, fallback = _SOURCE_DIRS[target]
    if _tem_documentos(primario):
        return primario
    return fallback


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingestao RAG (wrapper CI)")
    parser.add_argument(
        "--source",
        default=None,
        help=(
            "Diretorio de documentos. Precisa ser o diretorio efetivo de "
            "app.rag.ingest (data/knowledge_base ou o fallback "
            "data/sample_docs); divergencia falha em vez de ser ignorada."
        ),
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

    if args.source is not None:
        efetivo = _resolve_source_dir(target)
        if efetivo is None:
            print(
                "[scripts.ingest] --source nao pode ser validado para "
                f"--collection {args.collection!r} (target {target!r}); "
                "remova --source."
            )
            return 2
        informado = Path(args.source).resolve()
        if informado != efetivo.resolve():
            print(
                f"[scripts.ingest] --source={args.source!r} diverge do diretorio "
                f"efetivo {str(efetivo.relative_to(_REPO_ROOT))!r}; "
                "ajuste o argumento ou remova-o (o alvo e' escolhido por "
                "app.rag.ingest.resolve_source_dir)."
            )
            return 2

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
