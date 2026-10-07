"""Manifesto versionado do indice vetorial (validacao 2026-10-07, M-23).

O projeto citava quatro tamanhos incompativeis para o mesmo acervo de
referencia (28.962 chunks, ~767k pontos, 100.805 chunks, 4.019 entradas) e os
limiares de admissao (0,85 -> 0,665 do fallback; 0,62/0,45 do escalonamento)
diziam ter sido calibrados "contra o corpus" sem dizer QUAL. Sem um registro
do indice que estava no ar na hora da medicao, nao ha como saber se um limiar
ainda vale.

`data/index_manifest.json` guarda, por collection: quantos pontos, dimensao
do vetor denso, identidade do embedding (a mesma do embedding_guard),
diretorio de origem, arquivos processados e quando foi gerado. O ingest
atualiza a entrada da collection ao terminar; quem calibra um limiar cita o
`generated_at` e o `points` daqui. Versionar o arquivo e decisao de quem
ingere: ele descreve o SEU Qdrant, nao o do CI.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MANIFEST_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "index_manifest.json"


def collection_entry(client: Any, collection: str) -> dict[str, Any]:
    """Estado atual de uma collection no Qdrant (sem IO alem do Qdrant)."""
    from app.rag.embedding_guard import IDENTITY_COLLECTION, describe_dense_size, identity_point_id

    points = client.count(collection_name=collection, exact=True).count
    identity: dict[str, Any] = {}
    try:
        found = client.retrieve(
            collection_name=IDENTITY_COLLECTION,
            ids=[identity_point_id(collection)],
            with_payload=True,
        )
        if found:
            payload = found[0].payload or {}
            identity = {
                k: payload.get(k)
                for k in ("provider", "embedding_model", "dense_size")
                if k in payload
            }
    except Exception:
        logger.debug("identidade de %s nao encontrada", collection, exc_info=True)
    return {
        "points": int(points),
        "dense_size": describe_dense_size(client, collection),
        "embedding": identity or None,
    }


def update_manifest(
    client: Any,
    collection: str,
    *,
    target: str,
    source_dir: str | None,
    files_processed: int,
    path: Path = MANIFEST_PATH,
) -> dict[str, Any]:
    """Atualiza (ou cria) a entrada de `collection` no manifesto e devolve-a."""
    manifest: dict[str, Any] = {}
    if path.exists():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("manifesto ilegivel em %s - sera reescrito", path)
            manifest = {}
    collections = manifest.setdefault("collections", {})
    entry = collection_entry(client, collection)
    entry.update(
        {
            "target": target,
            "source_dir": source_dir,
            "files_processed": files_processed,
            "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        }
    )
    if source_dir:
        # Caminho relativo ao repo: o arquivo e versionado e nao deve carregar
        # o caminho absoluto (com o usuario) da maquina de quem ingeriu.
        try:
            entry["source_dir"] = str(Path(source_dir).resolve().relative_to(path.parent.parent))
        except ValueError:
            entry["source_dir"] = Path(source_dir).name
    collections[collection] = entry
    manifest["schema"] = 1
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8"
    )
    return entry
