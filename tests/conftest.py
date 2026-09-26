"""Configuracao compartilhada dos testes.

Testes marcados com @pytest.mark.integration sao pulados
automaticamente (nao falham) se a stack local (Qdrant e/ou Ollama)
nao estiver acessivel - isso evita falsos negativos em uma maquina
sem o ambiente de IA local rodando, e evita que o CI quebre por falta
de infraestrutura que so existe localmente.
"""

import os
import socket

# Isolamento do .env local: os testes de tests/test_api.py usam um
# TestClient sem header X-API-Key e dependem de API_KEY/A2A_API_KEY
# vazias no import do app (os testes de autenticacao configuram a chave
# explicitamente via monkeypatch). Com uma chave real no .env do
# desenvolvedor, 15 testes passavam a falhar com 401. Variaveis de
# ambiente tem precedencia sobre o .env no pydantic-settings, entao
# fixar vazio aqui (antes de importar app.*) torna a suite independente
# do .env de cada maquina.
os.environ["API_KEY"] = ""
os.environ["A2A_API_KEY"] = ""

import pytest  # noqa: E402

from app.connectors.base import connector_circuit_breaker  # noqa: E402
from app.events import idempotency  # noqa: E402
from app.rate_limit import limiter  # noqa: E402


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


@pytest.fixture(autouse=True)
def _reset_connector_circuit_breaker():
    """Avaliacao externa (medio prazo, item 3): mesmo motivo do reset
    do rate limiter acima - connector_circuit_breaker
    (app/connectors/base.py) e um singleton por processo, compartilhado
    por todos os testes da sessao do pytest. Um teste que simula N
    falhas consecutivas de rede pra um source_system (ex.: "abre o
    circuito apos N falhas") deixaria esse circuito aberto para
    qualquer teste seguinte do MESMO conector, se nao for resetado."""
    connector_circuit_breaker.reset()
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


@pytest.fixture(autouse=True)
def _isolate_event_idempotency(monkeypatch):
    """P1.1: forca o fallback em memoria (nunca toca um Redis real vindo
    do .env) e limpa os ids vistos entre testes - senao um cloudevents.id
    reutilizado em outro teste seria descartado como duplicata."""
    monkeypatch.setattr(idempotency, "_get_client", lambda: None)
    idempotency._local_seen.clear()
    yield
    idempotency._local_seen.clear()
