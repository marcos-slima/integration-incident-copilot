"""DA-69: checkpointing e execucao resumivel.

Usa o grafo REAL (`build_graph`) com MemorySaver e os nos substituidos por
fakes com contador - sem Qdrant, Ollama ou Postgres. O que se testa e o
contrato da DA-69: retomada pelo mesmo `thread_id` sem reexecutar nos ja
concluidos, isolamento entre threads, default desligado e o que o estado
persistido carrega.

O PostgresSaver real NAO e exercitado aqui (limite declarado na DA-69).
"""

import importlib
import sys
from collections import Counter

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.agent import graph

NODES = (
    "supervisor_node",
    "connector_node",
    "retrieve_node",
    "sap_diagnosis_node",
    "saas_diagnosis_node",
    "generic_diagnosis_node",
    "report_node",
)


class _Flaky:
    """Falha nas primeiras `fail_times` execucoes do no indicado."""

    def __init__(self, fail_on: str | None = None, fail_times: int = 1):
        self.calls: Counter = Counter()
        self.fail_on = fail_on
        self.fail_times = fail_times

    def node(self, name: str, update: dict):
        def _fn(state):
            self.calls[name] += 1
            if name == self.fail_on and self.calls[name] <= self.fail_times:
                raise RuntimeError(f"falha simulada em {name}")
            return update

        return _fn


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setattr(graph.settings, "graph_rag_enabled", False)
    monkeypatch.setattr(graph.settings, "ontology_enrichment_enabled", False)

    def install(fail_on=None):
        f = _Flaky(fail_on=fail_on)
        updates = {
            "supervisor_node": {"agent_domain": "sap"},
            "connector_node": {"connector_data": None},
            "retrieve_node": {"retrieved_context": [{"source": "doc-401.md"}]},
            "sap_diagnosis_node": {"diagnosis": {"probable_root_cause": "token expirado"}},
            "saas_diagnosis_node": {"diagnosis": {"probable_root_cause": "saas"}},
            "generic_diagnosis_node": {"diagnosis": {"probable_root_cause": "generic"}},
            "report_node": {"report_markdown": "# ok"},
        }
        for name in NODES:
            monkeypatch.setattr(graph, name, f.node(name, updates[name]))
        return f

    return install


def _state(**kw):
    base = {"description": "OData 401 apos refresh de token", "incident_id": "inc-1"}
    base.update(kw)
    return base


def _cfg(thread: str) -> dict:
    return {"configurable": {"thread_id": thread}}


def test_resume_skips_completed_nodes(fakes):
    f = fakes(fail_on="sap_diagnosis_node")
    g = graph.build_graph(checkpointer=MemorySaver())

    with pytest.raises(RuntimeError, match="sap_diagnosis_node"):
        g.invoke(_state(), _cfg("t-1"))
    assert f.calls["retrieve_node"] == 1

    # Retomada: input None + mesmo thread_id continua do ultimo checkpoint.
    final = g.invoke(None, _cfg("t-1"))

    assert final["report_markdown"] == "# ok"
    assert final["diagnosis"]["probable_root_cause"] == "token expirado"
    for done in ("supervisor_node", "connector_node", "retrieve_node"):
        assert f.calls[done] == 1, f"{done} foi reexecutado na retomada"
    assert f.calls["sap_diagnosis_node"] == 2  # falhou 1x, rodou 1x
    assert f.calls["report_node"] == 1


def test_without_checkpointer_a_rerun_starts_from_scratch(fakes):
    """Controle: sem checkpointer nao ha retomada - tudo roda de novo."""
    f = fakes(fail_on="sap_diagnosis_node")
    g = graph.build_graph(checkpointer=None)

    with pytest.raises(RuntimeError):
        g.invoke(_state())
    g.invoke(_state())
    assert f.calls["retrieve_node"] == 2


def test_threads_are_isolated(fakes):
    fakes()
    saver = MemorySaver()
    g = graph.build_graph(checkpointer=saver)

    g.invoke(_state(description="incidente A"), _cfg("t-a"))
    g.invoke(_state(description="incidente B"), _cfg("t-b"))

    a = g.get_state(_cfg("t-a")).values
    b = g.get_state(_cfg("t-b")).values
    assert a["description"] == "incidente A"
    assert b["description"] == "incidente B"
    assert g.get_state(_cfg("t-inexistente")).values == {}


def test_run_diagnosis_passes_thread_id_only_when_enabled(monkeypatch):
    from app.models import IncidentRequest

    seen = []

    def fake_invoke(initial_state, timeout_seconds=None, thread_id=None):
        seen.append((timeout_seconds, thread_id, initial_state["incident_id"]))
        return {**initial_state, "diagnosis": {}, "report_markdown": ""}

    monkeypatch.setattr(graph, "_invoke_graph_with_timeout", fake_invoke)
    monkeypatch.setattr(graph, "record_incident", lambda *a, **k: None, raising=False)

    req = IncidentRequest(description="OData 401 apos refresh de token")

    monkeypatch.setattr(graph.settings, "checkpointing_enabled", False)
    graph.run_diagnosis(req)
    monkeypatch.setattr(graph.settings, "checkpointing_enabled", True)
    graph.run_diagnosis(req)

    (t_off, thread_off, _), (t_on, thread_on, inc_on) = seen
    assert thread_off is None
    assert t_off is None and t_on is None  # thread_id nunca cai em timeout_seconds
    assert thread_on == {"configurable": {"thread_id": inc_on}}


def test_default_off_does_not_import_postgres_saver(monkeypatch):
    """Com CHECKPOINTING_ENABLED=false (default) o modulo nao importa o
    PostgresSaver - quem nao usa nao precisa de libpq/psycopg."""
    from app.config import Settings

    assert Settings(_env_file=None).checkpointing_enabled is False
    monkeypatch.setattr(graph.settings, "checkpointing_enabled", False)
    sys.modules.pop("langgraph.checkpoint.postgres", None)
    importlib.reload(graph)
    try:
        assert "langgraph.checkpoint.postgres" not in sys.modules
        assert not hasattr(graph, "PostgresSaver")
    finally:
        importlib.reload(graph)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "GAP DA-69: o estado inicial guarda description/logs crus; a redacao "
        "de PII (DA-30) so acontece ao montar o prompt. Com checkpointing "
        "ligado, e-mail/CPF ficam em claro no checkpoint. Quando a redacao "
        "for aplicada antes da persistencia este teste passa e o xfail "
        "estrito avisa para remover a marca."
    ),
)
def test_checkpoint_does_not_store_pii_in_clear(fakes):
    fakes()
    g = graph.build_graph(checkpointer=MemorySaver())
    g.invoke(
        _state(description="Falha para joao.silva@empresa.com.br CPF 123.456.789-09"),
        _cfg("t-pii"),
    )
    persisted = repr(g.get_state(_cfg("t-pii")).values)
    assert "joao.silva@empresa.com.br" not in persisted
    assert "123.456.789-09" not in persisted
