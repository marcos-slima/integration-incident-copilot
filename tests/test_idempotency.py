"""P1.1: idempotencia de eventos (app/events/idempotency.py) - claim antes
do processamento, release em caso de falha, Redis com fallback em memoria."""

from app.events import idempotency


class _FakeRedis:
    def __init__(self):
        self.keys: dict[str, int] = {}
        self.set_calls: list[dict] = []

    def set(self, key, value, nx=False, ex=None):
        self.set_calls.append({"key": key, "nx": nx, "ex": ex})
        if nx and key in self.keys:
            return None
        self.keys[key] = ex
        return True

    def delete(self, key):
        self.keys.pop(key, None)


def test_local_fallback_dedups_and_release_allows_redelivery():
    assert idempotency.is_duplicate("evt-a") is False
    assert idempotency.is_duplicate("evt-a") is True
    idempotency.release("evt-a")
    assert idempotency.is_duplicate("evt-a") is False


def test_event_without_id_is_never_deduplicated():
    assert idempotency.is_duplicate(None) is False
    assert idempotency.is_duplicate(None) is False
    assert idempotency.is_duplicate("") is False


def test_redis_path_shared_set_and_release(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(idempotency, "_get_client", lambda: fake)
    assert idempotency.is_duplicate("evt-r") is False
    assert idempotency.is_duplicate("evt-r") is True
    # claim e lease na mesma chamada atomica (SET NX EX), chave por evento
    assert fake.set_calls[0] == {
        "key": "events:seen:evt-r",
        "nx": True,
        "ex": idempotency._processing_lease_seconds(),
    }
    idempotency.release("evt-r")
    assert "events:seen:evt-r" not in fake.keys
    assert idempotency.is_duplicate("evt-r") is False


def test_mark_completed_keeps_event_deduplicated_for_24h(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(idempotency, "_get_client", lambda: fake)
    assert idempotency.is_duplicate("evt-d") is False
    idempotency.mark_completed("evt-d")
    assert fake.keys["events:seen:evt-d"] == idempotency._IDEMPOTENCY_TTL_SECONDS
    assert idempotency.is_duplicate("evt-d") is True


def test_expired_processing_lease_accepts_redelivery_after_crash(monkeypatch):
    """B-03: processo morreu apos o claim (sem release/mark_completed) -
    quando o lease expira, a reentrega volta a ser aceita."""
    monkeypatch.setattr(idempotency, "_processing_lease_seconds", lambda: -1)
    assert idempotency.is_duplicate("evt-crash") is False
    # ... crash: nenhum mark_completed/release ...
    assert idempotency.is_duplicate("evt-crash") is False


def test_local_completed_event_stays_duplicate_after_lease(monkeypatch):
    monkeypatch.setattr(idempotency, "_processing_lease_seconds", lambda: -1)
    assert idempotency.is_duplicate("evt-ok") is False
    idempotency.mark_completed("evt-ok")
    assert idempotency.is_duplicate("evt-ok") is True
