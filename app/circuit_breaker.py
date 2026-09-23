"""Circuit breaker generico com backend Redis (DA-41).

Anteriormente (DA-26): estado em-memoria, por processo — incompativel
com deploy Kyma com replicas > 1 (cada pod tem seu proprio estado,
portanto N pods podem abrir/fechar o circuito independentemente para
o mesmo provider, sem visibilidade cruzada).

DA-41 adiciona um backend Redis compartilhado: quando Redis esta
disponivel, o estado e distribuido entre todos os pods. Quando Redis
nao esta disponivel (REDIS_URL vazio ou Redis fora do ar), fallback
automatico para o comportamento em-memoria original — preserva o
principio "clone e rode" sem infra obrigatoria.

Comportamento do circuito (sem mudanca):
  closed → (N falhas consecutivas) → open → (cooldown expira) →
  deixa a proxima tentativa passar (half-open implicito) →
  sucesso reseta para closed, falha reabre.

Schema Redis (hash por chave logica):
  cb:<namespace>:<key> → { consecutive_failures: int, opened_at: float|"" }
  TTL = cooldown_seconds * 10 (renovado a cada write) — garante que
  entradas de providers "quietos" (nao usados) expirem naturalmente.

Namespace separa os dois usos independentes:
  "llm"   → providers de LLM (gateway.py)
  "conn"  → conectores externos (base.py)

Cada CircuitBreaker(namespace=...) instanciado e independente — mesmo
contrato da versao anterior, sem mudanca de API publica.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

_logger = logging.getLogger(__name__)

# Redis nao e importado no nivel de modulo — lazy init igual ao padrao
# do projeto (idempotency.py, a2a/task_store.py).
_redis_client = None
_redis_available: bool | None = None  # None = nao verificado ainda


def _get_redis_client():
    """Retorna cliente Redis compartilhado (lazy init, singleton).
    Retorna None se Redis nao estiver disponivel ou nao configurado."""
    global _redis_client, _redis_available

    if _redis_available is False:
        return None
    if _redis_client is not None:
        return _redis_client

    from app.config import settings

    if not settings.redis_url:
        _redis_available = False
        return None

    try:
        import redis

        _redis_client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        _redis_client.ping()
        _redis_available = True
        _logger.info("[circuit_breaker] Backend Redis ativo — estado distribuido entre pods.")
        return _redis_client
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "[circuit_breaker] Redis nao disponivel — fallback para estado "
            "em-memoria (nao compartilhado entre replicas Kyma). "
            "Configure REDIS_URL para estado distribuido. erro=%s",
            exc,
        )
        _redis_available = False
        return None


@dataclass
class _CircuitBreakerState:
    consecutive_failures: int = 0
    opened_at: float | None = None


class CircuitBreaker:
    """Circuit breaker com backend Redis (DA-41) e fallback em-memoria.

    threshold/cooldown sao passados em cada chamada (nao fixados no
    construtor) para respeitar overrides de config por chamada/teste —
    mesmo contrato da versao anterior.

    Args:
        namespace: prefixo logico para separar instancias no Redis
                   ("llm" para providers, "conn" para conectores).
    """

    def __init__(self, namespace: str = "default") -> None:
        self._namespace = namespace
        # Fallback em-memoria (usado quando Redis nao esta disponivel)
        self._states: dict[str, _CircuitBreakerState] = {}

    # ------------------------------------------------------------------
    # Helpers Redis
    # ------------------------------------------------------------------

    def _redis_key(self, key: str) -> str:
        return f"cb:{self._namespace}:{key}"

    def _read_state_redis(self, key: str) -> _CircuitBreakerState | None:
        """Le estado do Redis. Retorna None se Redis indisponivel ou chave inexistente."""
        client = _get_redis_client()
        if client is None:
            return None
        try:
            raw = client.hgetall(self._redis_key(key))
            if not raw:
                return _CircuitBreakerState()
            failures = int(raw.get(b"consecutive_failures", 0))
            opened_raw = raw.get(b"opened_at", b"")
            opened_at = float(opened_raw) if opened_raw else None
            return _CircuitBreakerState(
                consecutive_failures=failures,
                opened_at=opened_at,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "[circuit_breaker] Erro ao ler estado Redis key=%s — fallback em-memoria. erro=%s",
                key,
                exc,
            )
            return None

    def _write_state_redis(
        self, key: str, state: _CircuitBreakerState, cooldown_seconds: float
    ) -> bool:
        """Escreve estado no Redis com TTL. Retorna True se bem-sucedido."""
        client = _get_redis_client()
        if client is None:
            return False
        try:
            rkey = self._redis_key(key)
            mapping = {
                "consecutive_failures": state.consecutive_failures,
                "opened_at": state.opened_at if state.opened_at is not None else "",
            }
            client.hset(rkey, mapping=mapping)
            # TTL = cooldown * 10 para que entradas quietas expirem naturalmente
            ttl = max(int(cooldown_seconds * 10), 3600)
            client.expire(rkey, ttl)
            return True
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "[circuit_breaker] Erro ao escrever estado Redis key=%s. erro=%s",
                key,
                exc,
            )
            return False

    def _delete_state_redis(self, key: str) -> None:
        """Remove entrada do Redis (reset apos sucesso)."""
        client = _get_redis_client()
        if client is None:
            return
        try:
            client.delete(self._redis_key(key))
        except Exception:  # noqa: BLE001
            _logger.debug("[circuit_breaker] Nao foi possivel remover chave Redis key=%s.", key)

    # ------------------------------------------------------------------
    # Helpers em-memoria (fallback)
    # ------------------------------------------------------------------

    def _state_memory(self, key: str) -> _CircuitBreakerState:
        return self._states.setdefault(key, _CircuitBreakerState())

    # ------------------------------------------------------------------
    # API publica (mesmo contrato que a versao anterior)
    # ------------------------------------------------------------------

    def is_open(self, key: str, cooldown_seconds: float) -> bool:
        state = self._read_state_redis(key)
        if state is None:
            # Fallback em-memoria
            state = self._state_memory(key)
        if state.opened_at is None:
            return False
        return time.monotonic() - state.opened_at < cooldown_seconds

    def record_success(self, key: str) -> None:
        # Redis: remove a chave (estado limpo)
        self._delete_state_redis(key)
        # Tambem limpa fallback em-memoria
        self._states.pop(key, None)

    def record_failure(self, key: str, failure_threshold: int) -> None:
        # Tenta Redis primeiro
        state = self._read_state_redis(key)
        use_redis = state is not None
        if not use_redis:
            state = self._state_memory(key)

        state.consecutive_failures += 1
        if state.consecutive_failures >= failure_threshold:
            state.opened_at = time.monotonic()

        if use_redis:
            # cooldown_seconds nao e conhecido aqui — usa 300s como TTL base
            # (o TTL real sera renovado em is_open/record_failure subsequentes)
            self._write_state_redis(key, state, cooldown_seconds=300.0)
        else:
            self._states[key] = state

    def consecutive_failures(self, key: str) -> int:
        state = self._read_state_redis(key)
        if state is None:
            state = self._state_memory(key)
        return state.consecutive_failures

    def reset(self) -> None:
        """So para testes — limpa todo o estado (em-memoria e Redis namespace)."""
        self._states.clear()
        client = _get_redis_client()
        if client is None:
            return
        try:
            # Apaga todas as chaves do namespace deste CircuitBreaker
            pattern = f"cb:{self._namespace}:*"
            keys = client.keys(pattern)
            if keys:
                client.delete(*keys)
        except Exception:  # noqa: BLE001
            _logger.debug("[circuit_breaker] Nao foi possivel limpar chaves Redis namespace=%s.", self._namespace)
