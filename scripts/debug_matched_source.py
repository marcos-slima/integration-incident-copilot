#!/usr/bin/env python3
"""Diagnostico do matched_source nulo (eval 26/09/2026).

Roda o pipeline real para um caso e mostra, lado a lado:
  1. qual Qdrant foi usado (settings.qdrant_url)
  2. quais fontes o retrieval devolveu (retrieved_context)
  3. o matched_source final

Uso:
  uv run python3 scripts/debug_matched_source.py "iFlow falhando com erro 401"
  QDRANT_URL=http://localhost:6335 uv run python3 scripts/debug_matched_source.py "iFlow falhando com erro 401"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.graph import _invoke_graph_with_timeout  # noqa: E402
from app.config import settings  # noqa: E402

args = [a for a in sys.argv[1:] if a != "--debug"]
description = args[0] if args else "iFlow falhando com erro 401"
model = args[1] if len(args) > 1 else "qwen3-coder-next:latest"

print(f"Qdrant usado : {settings.qdrant_url}")
print(f"Modelo       : {model}")

state = {
    "description": description,
    "logs": None,
    "payload": None,
    "interface_type": None,
    "identifier": None,
    "llm_model": model,
    "debug": "--debug" in sys.argv,
    "incident_id": "debug-matched-source",
}
final = _invoke_graph_with_timeout(state)

hits = final.get("retrieved_context") or []
print(f"\nFontes recuperadas ({len(hits)}):")
for h in hits:
    print(f"  - {h.get('source')!r}  score={h.get('score', 0):.3f}  rerank={h.get('rerank_score')}")

diag = final.get("diagnosis", {})
print(f"\nmatched_source final: {diag.get('matched_source')!r}")
print(f"causa: {str(diag.get('probable_root_cause', ''))[:160]}")
