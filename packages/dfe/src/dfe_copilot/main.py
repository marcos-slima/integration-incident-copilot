"""Ponto de entrada do produto DF-e Copilot (DA-94).

`uvicorn dfe_copilot.main:app` sobe a API do core com o plugin DF-e ligado.
O plugin e' configurado ANTES de importar o core, porque o grafo e' montado
na importacao/primeira chamada.
"""

import os

os.environ.setdefault("DOMAIN_PLUGINS", "dfe_copilot.plugin")
os.environ.setdefault("DFE_KB_ENABLED", "true")  # base fiscal dfe_kb (P3)

from app.main import app

app.title = "DF-e Copilot"

__all__ = ["app"]
