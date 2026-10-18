"""DA-69: checkpointing e execucao resumivel.

Usa o grafo REAL (`build_graph`) com MemorySaver (com e sem o
RedactingMemorySaver) e os nos substituidos por
fakes com contador - sem Qdrant, Ollama ou Postgres. O que se testa e o
contrato da DA-69: retomada pelo mesmo `thread_id` sem reexecutar nos ja
concluidos, isolamento entre threads, default desligado e o que o estado
persistido carrega.

O PostgresSaver real NAO e exercitado aqui (limite declarado na DA-69).
"""

import dataclasses
import importlib
import sys
from collections import Counter

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.agent import checkpointing, graph
from app.agent.checkpointing import RedactingMemorySaver

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
    """Com CHECKPOINTING_ENABLED=false (default) nada do Postgres e
    importado - quem nao usa nao precisa de libpq/psycopg."""
    from app.config import Settings

    assert Settings(_env_file=None).checkpointing_enabled is False
    monkeypatch.setattr(graph.settings, "checkpointing_enabled", False)
    monkeypatch.setattr(graph, "_compiled_graph", None)
    sys.modules.pop("langgraph.checkpoint.postgres", None)
    importlib.reload(graph)
    try:
        graph.get_graph()
        assert "langgraph.checkpoint.postgres" not in sys.modules
        assert graph._checkpointer is None
    finally:
        importlib.reload(graph)


def test_get_graph_uses_redacting_postgres_builder_when_enabled(monkeypatch):
    sentinel = RedactingMemorySaver()
    urls = []

    def fake_builder(url):
        urls.append(url)
        return sentinel

    monkeypatch.setattr(checkpointing, "build_postgres_checkpointer", fake_builder)
    monkeypatch.setattr(graph.settings, "checkpointing_enabled", True)
    monkeypatch.setattr(graph.settings, "database_url", "postgresql+asyncpg://u:p@db:5432/iic")
    monkeypatch.setattr(graph, "_compiled_graph", None)
    monkeypatch.setattr(graph, "_checkpointer", None)

    graph.get_graph()

    assert urls == ["postgresql+asyncpg://u:p@db:5432/iic"]
    assert graph._checkpointer is sentinel
    monkeypatch.setattr(graph, "_compiled_graph", None)
    monkeypatch.setattr(graph, "_checkpointer", None)


def test_get_graph_requires_database_url(monkeypatch):
    monkeypatch.setattr(graph.settings, "checkpointing_enabled", True)
    monkeypatch.setattr(graph.settings, "database_url", "")
    monkeypatch.setattr(graph, "_compiled_graph", None)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        graph.get_graph()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgresql+asyncpg://u:p@h:5432/d", "postgresql://u:p@h:5432/d"),
        ("postgresql+psycopg2://u:p@h/d", "postgresql://u:p@h/d"),
        ("postgres://u@h/d", "postgresql://u@h/d"),
        ("postgresql://u@h/d", "postgresql://u@h/d"),
    ],
)
def test_libpq_url(raw, expected):
    assert checkpointing.libpq_url(raw) == expected


# ---------------------------------------------------------------------
# PII: o checkpoint nunca grava em claro (DA-30 aplicada na persistencia)
# ---------------------------------------------------------------------

EMAIL = "joao.silva@empresa.com.br"
CPF = "123.456.789-09"


def _raw_checkpoint_bytes(saver) -> bytes:
    """Tudo que o MemorySaver guardou, ja serializado (o que iria ao banco)."""
    blob = repr(saver.storage).encode() + repr(saver.writes).encode()
    if hasattr(saver, "blobs"):
        blob += repr(saver.blobs).encode()
    return blob


def test_checkpoint_does_not_store_pii_in_clear(fakes):
    fakes()
    saver = RedactingMemorySaver()
    g = graph.build_graph(checkpointer=saver)
    final = g.invoke(
        _state(description=f"Falha para {EMAIL} CPF {CPF}", logs=f"user={EMAIL}"),
        _cfg("t-pii"),
    )

    # Em memoria, durante a execucao, o fluxo ve o texto original.
    assert EMAIL in final["description"]

    raw = _raw_checkpoint_bytes(saver)
    assert EMAIL.encode() not in raw
    assert CPF.encode() not in raw
    persisted = repr(g.get_state(_cfg("t-pii")).values)
    assert EMAIL not in persisted and CPF not in persisted


def test_default_serializer_would_leak_pii(fakes):
    """Controle: sem a redacao o PII vai para o checkpoint -
    prova que o teste acima mede a redacao, nao uma coincidencia."""
    fakes()
    saver = MemorySaver()
    g = graph.build_graph(checkpointer=saver)
    g.invoke(_state(description=f"Falha para {EMAIL}"), _cfg("t-leak"))
    assert EMAIL in repr(g.get_state(_cfg("t-leak")).values)


def test_resume_reads_redacted_state_with_original_types(fakes, monkeypatch):
    """Retomada le o estado redigido e com os tipos originais
    (ConnectorResult continua dataclass, nao dict)."""
    from app.connectors.base import ConnectorResult

    f = fakes(fail_on="sap_diagnosis_node")
    connector = ConnectorResult(
        source_system="OData",
        status="error",
        error_code="401",
        message=f"token invalido para {EMAIL}",
        raw="{}",
        is_mock=False,
        is_fallback=False,
    )
    monkeypatch.setattr(
        graph, "connector_node", f.node("connector_node", {"connector_data": connector})
    )
    seen = {}

    def diagnose(state):
        f.calls["sap_diagnosis_node"] += 1
        if f.calls["sap_diagnosis_node"] == 1:
            raise RuntimeError("falha simulada")
        seen["connector"] = state["connector_data"]
        seen["description"] = state["description"]
        return {"diagnosis": {"probable_root_cause": "token"}}

    monkeypatch.setattr(graph, "sap_diagnosis_node", diagnose)
    g = graph.build_graph(checkpointer=RedactingMemorySaver())

    with pytest.raises(RuntimeError):
        g.invoke(_state(description=f"erro para {EMAIL}"), _cfg("t-types"))
    g.invoke(None, _cfg("t-types"))

    assert isinstance(seen["connector"], ConnectorResult)
    assert seen["connector"].is_mock is False
    assert EMAIL not in seen["connector"].message
    assert EMAIL not in seen["description"]
    assert f.calls["connector_node"] == 1


def test_redact_preserving_types_keeps_structure():
    from pydantic import BaseModel

    class M(BaseModel):
        texto: str
        n: int

    @dataclasses.dataclass(frozen=True)
    class D:
        a: str
        b: tuple

    value = {
        "s": f"mail {EMAIL}",
        "l": [CPF, 1],
        "t": (EMAIL,),
        "d": D(a=EMAIL, b=(CPF,)),
        "m": M(texto=EMAIL, n=3),
        "n": None,
    }
    out = checkpointing.redact_preserving_types(value)
    assert EMAIL not in out["s"] and CPF not in out["l"][0] and out["l"][1] == 1
    assert isinstance(out["t"], tuple) and EMAIL not in out["t"][0]
    assert isinstance(out["d"], D) and EMAIL not in out["d"].a and CPF not in out["d"].b[0]
    assert isinstance(out["m"], M) and EMAIL not in out["m"].texto and out["m"].n == 3
    assert out["n"] is None
