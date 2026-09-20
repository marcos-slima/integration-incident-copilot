"""Configuracao compartilhada dos testes.

Testes marcados com @pytest.mark.integration sao pulados
automaticamente (nao falham) se a stack local (Qdrant e/ou Ollama)
nao estiver acessivel - isso evita falsos negativos em uma maquina
sem o ambiente de IA local rodando, e evita que o CI quebre por falta
de infraestrutura que so existe localmente.
"""

import socket

import pytest

from app.rate_limit import limiter


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _stack_available() -> bool:
    qdrant_up = _port_open("127.0.0.1", 6333)
    ollama_up = _port_open("127.0.0.1", 11434)
    return qdrant_up and ollama_up


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Avaliacao externa (medio prazo, item 1): /a2a passou a ter seu
    proprio @limiter.limit("10/minute") (alem de /diagnose,
    /events/incident e /incidents/{id}/verify, que ja tinham o seu).
    O storage do slowapi Limiter e compartilhado por processo - sem
    reset, chamadas desses endpoints em testes ANTERIORES da mesma
    sessao do pytest contam para a cota do proximo teste (TestClient
    sempre usa o mesmo IP "testclient" como chave), causando 429
    "aleatorio" dependendo da ordem em que os testes rodam. Reseta
    antes de cada teste para isolar a cota entre eles."""
    limiter.reset()
    yield


def pytest_collection_modifyitems(config, items):
    if _stack_available():
        return
    skip_marker = pytest.mark.skip(
        reason="Stack local (Qdrant/Ollama) indisponivel em 127.0.0.1 - "
        "rode 'docker compose up -d' em ~/ai-stack e confirme o Ollama ativo"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_marker)
