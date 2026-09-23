"""Circuit breaker generico, in-memory, por processo - extraido de
app/llm/gateway.py (DA-26) para um modulo proprio, para que
app/connectors/*.py possa reusar a MESMA implementacao em vez de
duplicar a logica de "N falhas consecutivas abre o circuito por um
cooldown" (avaliacao externa - medio prazo, item 3: "Circuit breaker
nos conectores").

closed -> (N falhas consecutivas) -> open -> (cooldown expira) ->
deixa a proxima tentativa passar (half-open implicito) -> sucesso
reseta pra closed, falha reabre.

Cada `CircuitBreaker()` instanciado e independente (ex.: um para
providers de LLM, outro para conectores) - o estado e por instancia,
chaveado por um nome logico (provider, ou source_system do conector),
nao globalmente compartilhado entre os dois usos.

Nao-objetivo (mesmo do DA-26 original): nao e compartilhado entre
replicas/processos - precisaria de um backend compartilhado (Redis)
para isso em deploy multi-instancia.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class _CircuitBreakerState:
    consecutive_failures: int = 0
    opened_at: float | None = None


class CircuitBreaker:
    """threshold/cooldown sao passados em cada chamada (nao fixados no
    construtor) para respeitar overrides de config por chamada/teste."""

    def __init__(self) -> None:
        self._states: dict[str, _CircuitBreakerState] = {}

    def _state(self, key: str) -> _CircuitBreakerState:
        return self._states.setdefault(key, _CircuitBreakerState())

    def is_open(self, key: str, cooldown_seconds: float) -> bool:
        state = self._state(key)
        if state.opened_at is None:
            return False
        return time.monotonic() - state.opened_at < cooldown_seconds

    def record_success(self, key: str) -> None:
        self._states[key] = _CircuitBreakerState()

    def record_failure(self, key: str, failure_threshold: int) -> None:
        state = self._state(key)
        state.consecutive_failures += 1
        if state.consecutive_failures >= failure_threshold:
            state.opened_at = time.monotonic()

    def consecutive_failures(self, key: str) -> int:
        """Numero de falhas consecutivas para `key` - API publica para
        que chamadores (ex.: app/llm/gateway.py) nao precisem acessar
        `_state()` diretamente (atributo privado)."""
        return self._state(key).consecutive_failures

    def reset(self) -> None:
        """So para testes - limpa todo o estado do circuito."""
        self._states.clear()
