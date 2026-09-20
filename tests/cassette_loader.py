"""Helper de carregamento de cassettes de conectores (avaliacao
externa, medio prazo item 7) - modulo separado (nao dentro de
conftest.py) para poder ser importado normalmente por qualquer
arquivo de teste com "from cassette_loader import load_cassette",
sem depender de pytest ja ter processado fixtures."""

import json
from functools import cache
from pathlib import Path
from typing import Any

_CASSETTES_DIR = Path(__file__).parent / "cassettes"


@cache
def load_cassette(name: str) -> dict[str, Any]:
    """Le uma "cassette" (tests/cassettes/<name>.json - corpo de
    resposta HTTP real, documentado, de um conector) e devolve so o
    campo "response" (o metadado "_source", que documenta de onde
    veio o formato, fica de fora - ver tests/cassettes/README.md)."""
    path = _CASSETTES_DIR / f"{name}.json"
    data = json.loads(path.read_text())
    return data["response"]
