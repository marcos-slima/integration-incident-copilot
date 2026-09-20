"""Avaliacao externa (curto prazo, item 4) - timeout GLOBAL do
pipeline de diagnostico (app/agent/graph.py::_invoke_graph_with_timeout),
distinto do timeout de uma unica chamada LLM (ver
tests/test_llm_factory.py::test_ollama_uses_configured_request_timeout).

Usa um grafo FAKE (so implementa .invoke, dorme um tempo configuravel)
para provar a logica de watchdog sem depender de LangGraph/LLM real -
mesmo padrao de tests/test_nodes_multiagent.py (_StubGraphBare)."""

import time

import pytest

from app.exceptions import DiagnosisTimeoutError


class _SleepingGraph:
    def __init__(self, sleep_seconds: float):
        self.sleep_seconds = sleep_seconds

    def invoke(self, initial_state):
        time.sleep(self.sleep_seconds)
        return {"diagnosis": {}, "report_markdown": "ok"}


def test_invoke_graph_with_timeout_returns_result_when_fast_enough(monkeypatch):
    from app.agent import graph as graph_module
    from app.config import Settings

    monkeypatch.setattr(graph_module, "get_graph", lambda: _SleepingGraph(sleep_seconds=0.01))
    monkeypatch.setattr(graph_module, "settings", Settings(diagnosis_timeout_seconds=2.0))

    result = graph_module._invoke_graph_with_timeout({"description": "qualquer"})

    assert result == {"diagnosis": {}, "report_markdown": "ok"}


def test_invoke_graph_with_timeout_raises_when_pipeline_is_too_slow(monkeypatch):
    """O caso central desta correcao: o watchdog nao deixa o caller
    esperar alem de settings.diagnosis_timeout_seconds, mesmo que o
    grafo em si (aqui simulado com um sleep) nunca termine a tempo."""
    from app.agent import graph as graph_module
    from app.config import Settings

    monkeypatch.setattr(graph_module, "get_graph", lambda: _SleepingGraph(sleep_seconds=0.3))
    monkeypatch.setattr(graph_module, "settings", Settings(diagnosis_timeout_seconds=0.05))

    with pytest.raises(DiagnosisTimeoutError, match="0.05"):
        graph_module._invoke_graph_with_timeout({"description": "qualquer"})


def test_run_diagnosis_propagates_diagnosis_timeout_error(monkeypatch):
    """run_diagnosis() (a funcao publica chamada por /diagnose,
    /events/incident e pelo A2A task manager) precisa deixar o
    DiagnosisTimeoutError subir, nao mascara-lo - e o app/main.py que
    decide o que fazer com ele (504, ver tests/test_api.py)."""
    from app.agent import graph as graph_module
    from app.config import Settings
    from app.models import IncidentRequest

    monkeypatch.setattr(graph_module, "get_graph", lambda: _SleepingGraph(sleep_seconds=0.3))
    monkeypatch.setattr(graph_module, "settings", Settings(diagnosis_timeout_seconds=0.05))

    with pytest.raises(DiagnosisTimeoutError):
        graph_module.run_diagnosis(IncidentRequest(description="incidente qualquer"))
