"""Fila assincrona de diagnostico (RQ) - avaliacao externa (medio
prazo, item 6): "Fila assincrona (Celery/RQ/Arq) para diagnosticos
longos, com polling ou webhook". Reaproveita o mesmo Redis ja usado
pela persistencia de tasks A2A (app/a2a/task_store.py) - REDIS_URL
vazia desativa a feature: POST /diagnose continua sincrono (sem
mudanca de comportamento), e POST /diagnose/async fica indisponivel
(503, AsyncQueueUnavailableError) em vez de degradar silenciosamente
ou travar esperando um Redis que nao existe.

RQ (nao Celery/Arq) foi a escolha do usuario ("Redis (RQ) para fila +
persistencia A2A") - reaproveita a mesma infraestrutura opcional (um
unico Redis) em vez de exigir um broker separado, e a API
(`Queue.enqueue` / `Job.fetch`) e simples o suficiente para o volume
esperado de um projeto de portfolio.

Mesmo espirito de `RedisTaskStore` (app/a2a/task_store.py): uma classe
fina (`DiagnosisQueue`) que recebe a fila/o "job fetcher" injetados,
testavel sem Redis/RQ reais (ver tests/test_queue.py) - so os getters
usados em producao (`_get_redis_client`, `get_default_diagnosis_queue`)
precisam de infraestrutura de verdade, e importam `redis`/`rq` de
forma tardia pelo mesmo motivo de app/a2a/task_store.py e
app/rag/graph_store.py (nao exigir a dependencia/infra em nenhum
caminho default do projeto).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import Any, Protocol

from app.config import settings
from app.exceptions import ConfigurationError


class AsyncQueueUnavailableError(ConfigurationError):
    """Levantada quando REDIS_URL nao esta configurada e alguem tenta
    usar a fila assincrona (POST/GET /diagnose/async...). Traduzida
    para HTTP 503 pelos endpoints em app/main.py - e uma feature
    opcional nao ligada, nao um erro de servidor."""


class _JobLike(Protocol):
    id: str
    exc_info: str | None

    def get_status(self, refresh: bool = True) -> str: ...

    def return_value(self, refresh: bool = True) -> Any: ...


class _QueueLike(Protocol):
    def enqueue(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> _JobLike: ...


def run_diagnosis_job(request_data: dict[str, Any]) -> dict[str, Any]:
    """Funcao executada pelo worker RQ (processo separado, ver
    docker-compose.yml servico "worker") - precisa ser importavel por
    dotted-path ("app.queue.run_diagnosis_job"), pois o RQ resolve e
    importa a funcao pelo nome ao desserializar o job, nao pode ser
    uma closure/lambda.

    Recebe/devolve dicts (nao IncidentRequest/DiagnosisResponse
    diretamente): evita depender do pickle conseguir reconstruir um
    model Pydantic identico entre o processo da API (que enfileira) e
    o processo do worker (que consome) - eles rodam a mesma imagem
    Docker hoje, mas dicts de tipos nativos sao robustos a isso mesmo
    se um dia divergirem."""
    from app.agent.graph import run_diagnosis
    from app.models import IncidentRequest

    request = IncidentRequest(**request_data)
    return run_diagnosis(request).model_dump()


class DiagnosisQueue:
    """Camada fina sobre `Queue`/`Job` do RQ - so traduz
    enqueue/status para o formato usado pelos endpoints
    /diagnose/async (app/main.py). `job_fetcher` ja devolve None para
    job id inexistente (o `NoSuchJobError` do RQ e tratado dentro do
    fetcher de producao, `_default_job_fetcher` abaixo) - `DiagnosisQueue`
    em si nao conhece excecoes especificas do RQ, o que permite
    testa-la com fakes simples (ver tests/test_queue.py)."""

    def __init__(self, queue: _QueueLike, job_fetcher: Callable[[str], _JobLike | None]) -> None:
        self._queue = queue
        self._job_fetcher = job_fetcher

    def enqueue(self, request_data: dict[str, Any]) -> str:
        job = self._queue.enqueue(
            run_diagnosis_job,
            request_data,
            job_timeout=settings.diagnosis_timeout_seconds + 30,
        )
        return job.id

    def fetch_status(self, job_id: str) -> dict[str, Any] | None:
        job = self._job_fetcher(job_id)
        if job is None:
            return None
        job_status = job.get_status(refresh=True)
        result = job.return_value(refresh=True) if job_status == "finished" else None
        error = str(job.exc_info) if job_status == "failed" and job.exc_info else None
        return {"job_id": job.id, "status": job_status, "result": result, "error": error}


@lru_cache(maxsize=1)
def _get_redis_client():
    """Import tardio de `redis`, mesmo padrao de
    app/a2a/task_store.py::_get_redis_client - so acontece se
    REDIS_URL estiver configurada."""
    import redis

    return redis.Redis.from_url(settings.redis_url)


def _default_job_fetcher(job_id: str) -> _JobLike | None:
    from rq.exceptions import NoSuchJobError
    from rq.job import Job

    try:
        return Job.fetch(job_id, connection=_get_redis_client())
    except NoSuchJobError:
        return None


@lru_cache(maxsize=1)
def get_default_diagnosis_queue() -> DiagnosisQueue:
    from rq import Queue

    return DiagnosisQueue(
        queue=Queue("diagnosis", connection=_get_redis_client()),
        job_fetcher=_default_job_fetcher,
    )


def _require_redis_configured() -> None:
    if not settings.redis_url:
        raise AsyncQueueUnavailableError(
            "Fila assincrona de diagnostico requer REDIS_URL configurada "
            "(ver .env.example e docker-compose.yml, profile 'async')."
        )


def enqueue_diagnosis(request_data: dict[str, Any]) -> str:
    """Enfileira um diagnostico via RQ e devolve o job id. Levanta
    AsyncQueueUnavailableError se REDIS_URL nao estiver configurada."""
    _require_redis_configured()
    return get_default_diagnosis_queue().enqueue(request_data)


def get_job_status(job_id: str) -> dict[str, Any] | None:
    """Devolve o status atual de um job enfileirado
    ({"job_id", "status", "result", "error"}), ou None se o job_id nao
    existir (id invalido, ou job cujo resultado ja expirou - RQ
    mantem jobs finalizados por um TTL default, ver
    rq.Queue.enqueue/result_ttl). Levanta AsyncQueueUnavailableError
    se REDIS_URL nao estiver configurada."""
    _require_redis_configured()
    return get_default_diagnosis_queue().fetch_status(job_id)
