"""Testes de app/queue.py - sem Redis/RQ reais: fakes simples para
`_QueueLike`/`_JobLike` cobrem `DiagnosisQueue` (mesmo espirito de
`FakeRedis` em tests/test_a2a_task_store.py), e `enqueue_diagnosis`/
`get_job_status` sao testados via monkeypatch de
`get_default_diagnosis_queue` (mesmo padrao usado para
`_get_redis_client` em tests/test_a2a_task_store.py)."""

import pytest

import app.queue as queue_module
from app.config import Settings
from app.queue import (
    AsyncQueueUnavailableError,
    DiagnosisQueue,
    enqueue_diagnosis,
    get_job_status,
    run_diagnosis_job,
)


class FakeJob:
    def __init__(self, job_id: str, status: str, result=None, exc_info: str | None = None):
        self.id = job_id
        self._status = status
        self._result = result
        self.exc_info = exc_info

    def get_status(self, refresh: bool = True) -> str:
        return self._status

    def return_value(self, refresh: bool = True):
        return self._result


class FakeQueue:
    def __init__(self, job_to_return: FakeJob):
        self._job_to_return = job_to_return
        self.enqueue_calls: list[tuple] = []

    def enqueue(self, func, *args, **kwargs):
        self.enqueue_calls.append((func, args, kwargs))
        return self._job_to_return


def test_diagnosis_queue_enqueue_passes_run_diagnosis_job_and_returns_job_id():
    fake_job = FakeJob("job-1", status="queued")
    fake_queue = FakeQueue(fake_job)
    queue = DiagnosisQueue(queue=fake_queue, job_fetcher=lambda job_id: None)

    job_id = queue.enqueue({"description": "iFlow falhando"})

    assert job_id == "job-1"
    assert len(fake_queue.enqueue_calls) == 1
    func, args, kwargs = fake_queue.enqueue_calls[0]
    assert func is run_diagnosis_job
    assert args == ({"description": "iFlow falhando"},)
    assert "job_timeout" in kwargs


def test_diagnosis_queue_fetch_status_returns_none_when_job_fetcher_returns_none():
    queue = DiagnosisQueue(queue=FakeQueue(FakeJob("x", "queued")), job_fetcher=lambda job_id: None)
    assert queue.fetch_status("nao-existe") is None


def test_diagnosis_queue_fetch_status_finished_includes_result_and_no_error():
    fake_job = FakeJob("job-2", status="finished", result={"probable_root_cause": "X"})
    queue = DiagnosisQueue(queue=FakeQueue(fake_job), job_fetcher=lambda job_id: fake_job)

    status = queue.fetch_status("job-2")

    assert status == {
        "job_id": "job-2",
        "status": "finished",
        "result": {"probable_root_cause": "X"},
        "error": None,
    }


def test_diagnosis_queue_fetch_status_failed_includes_error_and_no_result():
    fake_job = FakeJob("job-3", status="failed", exc_info="Traceback: boom")
    queue = DiagnosisQueue(queue=FakeQueue(fake_job), job_fetcher=lambda job_id: fake_job)

    status = queue.fetch_status("job-3")

    assert status["status"] == "failed"
    assert status["result"] is None
    assert status["error"] == "Traceback: boom"


def test_diagnosis_queue_fetch_status_queued_has_no_result_or_error():
    fake_job = FakeJob("job-4", status="queued")
    queue = DiagnosisQueue(queue=FakeQueue(fake_job), job_fetcher=lambda job_id: fake_job)

    status = queue.fetch_status("job-4")

    assert status["status"] == "queued"
    assert status["result"] is None
    assert status["error"] is None


def test_enqueue_diagnosis_raises_when_redis_url_not_configured(monkeypatch):
    monkeypatch.setattr(queue_module, "settings", Settings(redis_url=""))

    with pytest.raises(AsyncQueueUnavailableError):
        enqueue_diagnosis({"description": "x"})


def test_get_job_status_raises_when_redis_url_not_configured(monkeypatch):
    monkeypatch.setattr(queue_module, "settings", Settings(redis_url=""))

    with pytest.raises(AsyncQueueUnavailableError):
        get_job_status("job-1")


def test_enqueue_diagnosis_delegates_to_default_queue_when_configured(monkeypatch):
    fake_job = FakeJob("job-5", status="queued")
    fake_diagnosis_queue = DiagnosisQueue(
        queue=FakeQueue(fake_job), job_fetcher=lambda job_id: None
    )

    monkeypatch.setattr(queue_module, "settings", Settings(redis_url="redis://127.0.0.1:6379/0"))
    monkeypatch.setattr(queue_module, "get_default_diagnosis_queue", lambda: fake_diagnosis_queue)

    job_id = enqueue_diagnosis({"description": "x"})

    assert job_id == "job-5"


def test_get_job_status_delegates_to_default_queue_when_configured(monkeypatch):
    fake_job = FakeJob("job-6", status="finished", result={"probable_root_cause": "Y"})
    fake_diagnosis_queue = DiagnosisQueue(
        queue=FakeQueue(fake_job), job_fetcher=lambda job_id: fake_job
    )

    monkeypatch.setattr(queue_module, "settings", Settings(redis_url="redis://127.0.0.1:6379/0"))
    monkeypatch.setattr(queue_module, "get_default_diagnosis_queue", lambda: fake_diagnosis_queue)

    status = get_job_status("job-6")

    assert status["status"] == "finished"
    assert status["result"] == {"probable_root_cause": "Y"}


def test_run_diagnosis_job_reconstructs_request_and_serializes_response(monkeypatch):
    """run_diagnosis_job e o que o worker RQ efetivamente executa -
    prova que ele reconstroi IncidentRequest a partir de um dict puro
    e devolve DiagnosisResponse tambem como dict (nao o model
    Pydantic), ver docstring do modulo."""
    from app.models import DiagnosisResponse, IncidentRequest

    captured_request = {}

    def _fake_run_diagnosis(request):
        captured_request["request"] = request
        return DiagnosisResponse(
            probable_root_cause="Causa raiz stub",
            confidence=0.5,
            next_steps=["Passo 1"],
            report_markdown="## Diagnostico",
        )

    monkeypatch.setattr("app.agent.graph.run_diagnosis", _fake_run_diagnosis)

    result = run_diagnosis_job({"description": "iFlow com erro 500"})

    assert isinstance(captured_request["request"], IncidentRequest)
    assert captured_request["request"].description == "iFlow com erro 500"
    assert result["probable_root_cause"] == "Causa raiz stub"
