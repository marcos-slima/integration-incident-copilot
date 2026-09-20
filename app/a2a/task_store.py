"""Armazenamento das tasks A2A - abstrai onde `A2ATask` fica guardada,
para o `TaskManager` (app/a2a/task_manager.py) nao precisar saber se o
backend e um dict em memoria ou Redis.

Avaliacao externa (medio prazo, item 2): "Persistencia de tasks A2A
(Redis/SQLite) e estados assincronos se o protocolo for evoluido".
Escolhido Redis (nao SQLite) porque o mesmo Redis tambem passa a
servir a fila assincrona de diagnostico (RQ, ver app/queue.py) - um
unico backend de infraestrutura opcional para os dois usos, em vez de
dois (SQLite + um broker separado para a fila).

Sem REDIS_URL configurada (default), `get_default_task_store()`
devolve `InMemoryTaskStore` - mesmo comportamento de antes desta
mudanca (perde as tasks a cada restart do processo), preservando o
"clone e rode" sem infraestrutura obrigatoria (mesmo principio do
GraphRAG, app/rag/graph_store.py).

Mesmo espirito de `_Neo4jSession` (app/rag/graph_store.py) e do
`httpx.MockTransport` dos conectores HTTP: um Protocol pequeno,
testavel com um fake sem precisar de infraestrutura real (ver
tests/test_a2a_task_store.py).
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import TYPE_CHECKING, Protocol

from app.config import settings

if TYPE_CHECKING:
    from app.a2a.task_manager import A2ATask

# Prefixo de namespace no Redis - evita colisao se o mesmo Redis for
# reaproveitado por outra parte do app (ex.: broker da fila RQ, que
# usa suas proprias chaves "rq:*") ou por outro projeto no mesmo
# servidor Redis compartilhado.
_REDIS_KEY_PREFIX = "a2a:task:"

# TTL das tasks no Redis - 24h. Tasks A2A sao resultado de diagnostico
# (texto + metadados), nao um registro de auditoria de longo prazo (o
# GraphRAG, quando habilitado, e quem grava o historico duradouro via
# upsert_incident_graph) - sem TTL, o Redis cresceria sem limite com
# tasks que ninguem mais consulta.
_REDIS_TASK_TTL_SECONDS = 24 * 60 * 60


class TaskStore(Protocol):
    """Subconjunto minimo de operacoes que o TaskManager precisa -
    'get' e 'set' por id, nada de listagem/paginacao (o protocolo A2A
    implementado aqui nao expoe um endpoint "listar tasks", ver
    app/a2a/server.py)."""

    def get(self, task_id: str) -> A2ATask | None: ...

    def set(self, task: A2ATask) -> None: ...


class InMemoryTaskStore:
    """Comportamento historico (pre-medio-prazo item 2): dict simples,
    perdido a cada restart do processo. Usado quando REDIS_URL nao
    esta configurada."""

    def __init__(self) -> None:
        self._tasks: dict[str, A2ATask] = {}

    def get(self, task_id: str) -> A2ATask | None:
        return self._tasks.get(task_id)

    def set(self, task: A2ATask) -> None:
        self._tasks[task.id] = task


class RedisTaskStore:
    """Serializa `A2ATask` (incluindo o `DiagnosisResponse` aninhado em
    `.result`, um model Pydantic) para JSON. `client` e injetavel para
    os testes usarem um fake Redis (dict + TTL simulado) sem precisar
    de um Redis real no ar - mesmo padrao de injecao de dependencia
    usado em `_get_session`/`FakeSession` (app/rag/graph_store.py) e
    nos conectores HTTP."""

    def __init__(self, client) -> None:
        self._client = client

    def get(self, task_id: str) -> A2ATask | None:
        raw = self._client.get(_REDIS_KEY_PREFIX + task_id)
        if raw is None:
            return None
        return self._deserialize(raw)

    def set(self, task: A2ATask) -> None:
        self._client.set(
            _REDIS_KEY_PREFIX + task.id,
            self._serialize(task),
            ex=_REDIS_TASK_TTL_SECONDS,
        )

    @staticmethod
    def _serialize(task: A2ATask) -> str:
        payload = {
            "id": task.id,
            "state": task.state,
            "input_description": task.input_description,
            "result": task.result.model_dump() if task.result is not None else None,
            "error": task.error,
        }
        return json.dumps(payload)

    @staticmethod
    def _deserialize(raw: str | bytes) -> A2ATask:
        from app.a2a.task_manager import A2ATask
        from app.models import DiagnosisResponse

        data = json.loads(raw)
        result = DiagnosisResponse(**data["result"]) if data["result"] is not None else None
        return A2ATask(
            id=data["id"],
            state=data["state"],
            input_description=data["input_description"],
            result=result,
            error=data["error"],
        )


@lru_cache(maxsize=1)
def _get_redis_client():
    """Import tardio de `redis` - so acontece se REDIS_URL estiver
    configurada, mesmo motivo do `_get_driver()` em
    app/rag/graph_store.py (nao exigir a dependencia/infra em nenhum
    caminho default do projeto)."""
    import redis

    return redis.Redis.from_url(settings.redis_url)


def get_default_task_store() -> TaskStore:
    if not settings.redis_url:
        return InMemoryTaskStore()
    return RedisTaskStore(_get_redis_client())
