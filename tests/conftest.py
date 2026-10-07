"""Configuracao compartilhada dos testes.

Testes marcados com @pytest.mark.integration sao pulados
automaticamente (nao falham) se a stack local (Qdrant e/ou Ollama)
nao estiver acessivel - isso evita falsos negativos em uma maquina
sem o ambiente de IA local rodando, e evita que o CI quebre por falta
de infraestrutura que so existe localmente.
"""

import os
import socket
from pathlib import Path

# Carregar variáveis do .env se existir (antes de qualquer import)
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    from dotenv import load_dotenv

    load_dotenv(env_path)

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
os.environ["ADMIN_API_KEY"] = ""  # DA-46/47/48: superficie admin no mesmo regime de isolamento
# DA-55: mesmo motivo, agora para o banco. Com DATABASE_URL real no .env da
# maquina (homologacao aponta para o postgres do compose), os testes que
# esperam "sem banco" (test_admin_routes::test_models_list_sem_banco_503,
# login sem usuarios, etc.) passavam a depender do host `postgres` do
# compose — que nao resolve fora dele (socket.gaierror). Testes que
# precisam de banco usam fixture SQLite em memoria (padrao test_admin_*).
os.environ["DATABASE_URL"] = ""

import pytest

from app import auth_guard
from app.connectors.base import connector_circuit_breaker
from app.events import idempotency
from app.rate_limit import limiter


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _qdrant_port() -> int:
    return int(os.environ.get("QDRANT_HOST_PORT", "6333"))


def _stack_available() -> bool:
    qdrant_up = _port_open("127.0.0.1", _qdrant_port())
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
        "rode 'docker compose up -d' na raiz do projeto e confirme o Ollama ativo"
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


@pytest.fixture(autouse=True)
def _isolate_auth_guard(monkeypatch):
    """SEC-03: contadores de falha e revogacoes de sessao (app/auth_guard.py)
    sao por processo - sem reset, as chaves invalidas de um teste levariam o
    seguinte a 429. Forca o fallback em memoria (nunca o Redis do .env)."""
    monkeypatch.setattr(auth_guard, "_redis", lambda: None)
    auth_guard.reset_for_tests()
    yield
    auth_guard.reset_for_tests()


@pytest.fixture(autouse=True)
def _isolate_oauth_token_cache():
    """M-06: o cache de token OAuth2 e por processo - sem limpar, o token de
    um teste (MockTransport) seria reaproveitado no seguinte e testes de
    falha do token endpoint nunca chegariam a chamar o endpoint."""
    from app.connectors.base import oauth_token_cache

    oauth_token_cache.clear()
    yield
    oauth_token_cache.clear()
