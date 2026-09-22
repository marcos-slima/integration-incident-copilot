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


def test_in_memory_task_store_evicts_oldest_when_max_reached():
    """Quando o limite e atingido, a task mais antiga (FIFO) deve ser descartada."""
    store = InMemoryTaskStore(max_tasks=3)
    from app.a2a.task_manager import A2ATask

    t1 = A2ATask(id="t1", state="completed", input_description="x")
    t2 = A2ATask(id="t2", state="completed", input_description="y")
    t3 = A2ATask(id="t3", state="completed", input_description="z")
    t4 = A2ATask(id="t4", state="completed", input_description="w")

    store.set(t1)
    store.set(t2)
    store.set(t3)
    # Neste ponto o store esta cheio (3 tasks)
    assert store.get("t1") is not None

    # Inserir t4 deve descartar t1 (mais antiga)
    store.set(t4)
    assert store.get("t1") is None
    assert store.get("t2") is not None
    assert store.get("t3") is not None
    assert store.get("t4") is not None


def test_in_memory_task_store_update_does_not_evict():
    """Atualizar uma task ja existente nao deve contar como nova insercao."""
    store = InMemoryTaskStore(max_tasks=2)
    from app.a2a.task_manager import A2ATask

    t1 = A2ATask(id="t1", state="working", input_description="x")
    t2 = A2ATask(id="t2", state="working", input_description="y")

    store.set(t1)
    store.set(t2)

    # Atualizar t1 (ja existente) - nao deve expulsar nenhuma task
    t1_updated = A2ATask(id="t1", state="completed", input_description="x")
    store.set(t1_updated)

    assert store.get("t1").state == "completed"
    assert store.get("t2") is not None


def test_in_memory_task_store_default_limit_is_large():
    """O limite default deve ser alto o suficiente para uso normal."""
    from app.a2a.task_store import _IN_MEMORY_MAX_TASKS

    assert _IN_MEMORY_MAX_TASKS >= 100
