"""Ponto de entrada do produto DF-e Copilot (DA-94).

`uvicorn dfe_copilot.main:app` sobe a API do core com o plugin DF-e ligado.
O plugin e' configurado ANTES de importar o core, porque o grafo e' montado
na importacao/primeira chamada.
"""

import os
from pathlib import Path

os.environ.setdefault("DOMAIN_PLUGINS", "dfe_copilot.plugin")
os.environ.setdefault("SESSION_COOKIE_NAME", "dfe_session")
os.environ.setdefault("DFE_KB_ENABLED", "true")  # base fiscal dfe_kb (P3)
# DA-95: o produto serve o proprio shell de UI (packages/dfe/frontend/dist).
os.environ.setdefault("UI_DIST_DIR", str(Path(__file__).resolve().parents[2] / "frontend" / "dist"))

from app.sdk import SDK_CONTRACT_VERSION, asgi_app, compatible_version

if not compatible_version("1.0", SDK_CONTRACT_VERSION):
    raise RuntimeError("Contrato SDK incompatível com o produto")

app = asgi_app()
app.title = "DF-e Copilot"

__all__ = ["app"]
