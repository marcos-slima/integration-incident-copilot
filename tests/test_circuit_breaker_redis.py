"""Caminho Redis do CircuitBreaker (DA-41) e fallbacks em-memoria.

IIC-AUD-108: `app/circuit_breaker.py` estava em 53% de cobertura - so o
fallback em-memoria era exercitado. Aqui o Redis e' um fake em processo
(sem rede), cobrindo leitura/escrita/remocao, o script Lua de
`record_failure` e a degradacao quando o Redis falha no meio da chamada.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

import app.circuit_breaker as cb


class _FakeRedis:
    def __init__(self, fail: bool = False) -> None:
        self.data: dict[str, dict[bytes, bytes]] = {}
        self.fail = fail
        self.expires: dict[str, int] = {}

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("redis caiu")

    def ping(self) -> bool:
        self._check()
        return True

    def hgetall(self, key):
        self._check()
        return self.data.get(key, {})

    def hset(self, key, mapping):
        self._check()
        self.data[key] = {k.encode(): str(v).encode() for k, v in mapping.items()}

    def expire(self, key, ttl):
        self._check()
        self.expires[key] = ttl

    def delete(self, *keys):
        self._check()
        for k in keys:
            self.data.pop(k, None)

    def keys(self, pattern):
        self._check()
        prefix = pattern.rstrip("*")
        return [k for k in self.data if k.startswith(prefix)]

    def eval(self, _script, _n, key, threshold, ttl, now):
        # Mesma semantica do _LUA_RECORD_FAILURE: incrementa e abre no limiar.
        self._check()
        st = self.data.setdefault(key, {})
        failures = int(st.get(b"consecutive_failures", b"0")) + 1
        st[b"consecutive_failures"] = str(failures).encode()
        if failures >= int(threshold) and not st.get(b"opened_at"):
            st[b"opened_at"] = now.encode()
        self.expires[key] = int(ttl)
        return [str(failures).encode(), st.get(b"opened_at", b"")]


@pytest.fixture
def fake_redis(monkeypatch):
    client = _FakeRedis()
    monkeypatch.setattr(cb, "_redis_client", client)
    monkeypatch.setattr(cb, "_redis_available", True)
    return client


def test_record_failure_abre_circuito_via_lua(fake_redis):
    breaker = cb.CircuitBreaker("llm")
    breaker.record_failure("openai", failure_threshold=2, cooldown_seconds=30)
    assert not breaker.is_open("openai", cooldown_seconds=30)
    assert breaker.consecutive_failures("openai") == 1

    breaker.record_failure("openai", failure_threshold=2, cooldown_seconds=30)
    assert breaker.is_open("openai", cooldown_seconds=30)
    assert breaker.consecutive_failures("openai") == 2
    # TTL minimo de 1h mesmo com cooldown curto
    assert fake_redis.expires["cb:llm:openai"] == 3600


def test_record_success_remove_chave_redis(fake_redis):
    breaker = cb.CircuitBreaker("conn")
    breaker.record_failure("sf", failure_threshold=1)
    assert "cb:conn:sf" in fake_redis.data
    breaker.record_success("sf")
    assert "cb:conn:sf" not in fake_redis.data
    assert breaker.consecutive_failures("sf") == 0


def test_write_state_redis_grava_mapping_e_ttl(fake_redis):
    breaker = cb.CircuitBreaker("x")
    ok = breaker._write_state_redis(
        "k", cb._CircuitBreakerState(consecutive_failures=3, opened_at=None), 1000
    )
    assert ok
    assert fake_redis.data["cb:x:k"][b"consecutive_failures"] == b"3"
    assert fake_redis.expires["cb:x:k"] == 10000


def test_cooldown_expirado_fecha_circuito(fake_redis):
    breaker = cb.CircuitBreaker("x")
    fake_redis.data["cb:x:k"] = {
        b"consecutive_failures": b"5",
        b"opened_at": str(time.time() - 100).encode(),
    }
    assert not breaker.is_open("k", cooldown_seconds=10)
    assert breaker.is_open("k", cooldown_seconds=1000)


def test_reset_apaga_so_o_namespace(fake_redis):
    a, b = cb.CircuitBreaker("a"), cb.CircuitBreaker("b")
    a.record_failure("k", failure_threshold=1)
    b.record_failure("k", failure_threshold=1)
    a.reset()
    assert "cb:a:k" not in fake_redis.data
    assert "cb:b:k" in fake_redis.data


def test_redis_falhando_degrada_para_memoria(fake_redis):
    breaker = cb.CircuitBreaker("x")
    fake_redis.fail = True
    breaker.record_failure("k", failure_threshold=1)  # Lua falha -> memoria
    assert breaker.is_open("k", cooldown_seconds=60)  # leitura falha -> memoria
    assert breaker.consecutive_failures("k") == 1
    assert breaker._write_state_redis("k", cb._CircuitBreakerState(), 60) is False
    breaker.record_success("k")  # delete falha sem propagar
    breaker.reset()  # keys falha sem propagar
    assert breaker.consecutive_failures("k") == 0


def test_get_redis_client_sem_url_desliga(monkeypatch):
    monkeypatch.setattr(cb, "_redis_client", None)
    monkeypatch.setattr(cb, "_redis_available", None)
    import app.config

    monkeypatch.setattr(app.config, "settings", SimpleNamespace(redis_url=""))
    assert cb._get_redis_client() is None
    assert cb._redis_available is False
    # memoizado: nao reconsulta settings
    assert cb._get_redis_client() is None


def test_get_redis_client_ping_falha_desliga(monkeypatch):
    import redis

    import app.config

    monkeypatch.setattr(cb, "_redis_client", None)
    monkeypatch.setattr(cb, "_redis_available", None)
    monkeypatch.setattr(app.config, "settings", SimpleNamespace(redis_url="redis://127.0.0.1:1/0"))
    monkeypatch.setattr(redis.Redis, "from_url", lambda *a, **kw: _FakeRedis(fail=True))
    assert cb._get_redis_client() is None
    assert cb._redis_available is False


def test_get_redis_client_ok_reutiliza_singleton(monkeypatch):
    import redis

    import app.config

    fake = _FakeRedis()
    monkeypatch.setattr(cb, "_redis_client", None)
    monkeypatch.setattr(cb, "_redis_available", None)
    monkeypatch.setattr(
        app.config, "settings", SimpleNamespace(redis_url="redis://127.0.0.1:6379/0")
    )
    monkeypatch.setattr(redis.Redis, "from_url", lambda *a, **kw: fake)
    assert cb._get_redis_client() is fake
    assert cb._get_redis_client() is fake
    assert cb._redis_available is True
