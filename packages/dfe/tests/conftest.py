"""Isolamento do produto pelo contrato público de testes do core (DA-95).

Funciona com o SDK instalado; nenhum acesso ao conftest do checkout do IIC.
"""

from app.sdk.pytest_plugin import (  # noqa: F401
    _isolate_auth_guard,
    _isolate_event_idempotency,
    _isolate_oauth_token_cache,
    _reset_auth_guard,
    _reset_connector_circuit_breaker,
    _reset_rate_limiter,
)
