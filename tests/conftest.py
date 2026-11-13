"""Configuracao compartilhada dos testes.

Testes marcados com @pytest.mark.integration sao pulados
automaticamente (nao falham) se a stack local (Qdrant e/ou Ollama)
nao estiver acessivel - isso evita falsos negativos em uma maquina
sem o ambiente de IA local rodando, e evita que o CI quebre por falta
de infraestrutura que so existe localmente.
"""

from pathlib import Path

# Carregar variáveis do .env se existir (antes de qualquer import)
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    from dotenv import load_dotenv

    load_dotenv(env_path)

from app.sdk.pytest_plugin import (  # noqa: F401
    _isolate_auth_guard,
    _isolate_event_idempotency,
    _isolate_oauth_token_cache,
    _reset_auth_guard,
    _reset_connector_circuit_breaker,
    _reset_rate_limiter,
    pytest_collection_modifyitems,
)
