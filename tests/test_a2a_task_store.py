"""Testes de app/a2a/task_store.py - sem Redis real: um FakeRedis
minimo (dict + TTL registrado, mas nao expirado de verdade) cobre a
serializacao/desserializacao de `A2ATask`, mesmo espirito do
`FakeSession` usado em tests/test_graph_store.py."""

from app.a2a.task_manager import A2ATask
from app.a2a.task_store import InMemoryTaskStore, RedisTaskStore, get_default_task_store
from app.config import Settings
from app.models import DiagnosisResponse


class FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int | None]] = []

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value
        self.set_calls.append((key, value, ex))


def _sample_task(task_id: str = "t1") -> A2ATask:
    return A2ATask(
        id=task_id,
        state="completed",
        input_description="iFlow falhando com 401",
        result=DiagnosisResponse(
            probable_root_cause="Certificado expirado",
            confidence=0.8,
            next_steps=["Renovar certificado"],
            report_markdown="## Diagnostico\n\nCertificado expirado",
            matched_source="cpi_http_401.md",
        ),
        error=None,
    )


def test_in_memory_task_store_roundtrip():
    store = InMemoryTaskStore()
    task = _sample_task()

    assert store.get(task.id) is None
    store.set(task)

    fetched = store.get(task.id)
    assert fetched is task


def test_in_memory_task_store_missing_id_returns_none():
    store = InMemoryTaskStore()
    assert store.get("nao-existe") is None


def test_redis_task_store_roundtrip_preserves_all_fields():
    client = FakeRedis()
    store = RedisTaskStore(client)
    task = _sample_task()

    store.set(task)
    fetched = store.get(task.id)

    assert fetched is not None
    assert fetched is not task  # veio de-serializado, nao a mesma instancia
    assert fetched.id == task.id
    assert fetched.state == task.state
    assert fetched.input_description == task.input_description
    assert fetched.error == task.error
    assert fetched.result == task.result


def test_redis_task_store_roundtrip_with_no_result_and_error():
    client = FakeRedis()
    store = RedisTaskStore(client)
    task = A2ATask(id="t2", state="failed", input_description="x", result=None, error="boom")

    store.set(task)
    fetched = store.get(task.id)

    assert fetched.result is None
    assert fetched.error == "boom"


def test_redis_task_store_missing_key_returns_none():
    client = FakeRedis()
    store = RedisTaskStore(client)
    assert store.get("nao-existe") is None


def test_redis_task_store_uses_namespaced_key_and_ttl():
    client = FakeRedis()
    store = RedisTaskStore(client)
    task = _sample_task()

    store.set(task)

    assert len(client.set_calls) == 1
    key, _value, ttl = client.set_calls[0]
    assert key == "a2a:task:t1"
    assert ttl == 24 * 60 * 60


def test_get_default_task_store_returns_in_memory_when_redis_url_empty(monkeypatch):
    monkeypatch.setattr("app.a2a.task_store.settings", Settings(redis_url=""))
    assert isinstance(get_default_task_store(), InMemoryTaskStore)


def test_get_default_task_store_returns_redis_backed_when_configured(monkeypatch):
    monkeypatch.setattr(
        "app.a2a.task_store.settings", Settings(redis_url="redis://127.0.0.1:6379/0")
    )
    monkeypatch.setattr("app.a2a.task_store._get_redis_client", lambda: FakeRedis())

    store = get_default_task_store()
    assert isinstance(store, RedisTaskStore)
