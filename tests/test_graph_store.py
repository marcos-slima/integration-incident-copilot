"""Testes do GraphRAG (app/rag/graph_store.py) - sem Neo4j real: usa
uma sessao FAKE que implementa so o metodo `.run()` (mesmo espirito do
`httpx.MockTransport` usado nos conectores HTTP), verificando que as
queries Cypher corretas sao disparadas e que os dados voltam mapeados
certo, sem depender de infraestrutura externa.
"""

from app.config import Settings
from app.rag.graph_store import (
    ensure_constraints,
    format_graph_context_for_prompt,
    graph_context,
    is_enabled,
    upsert_incident_graph,
)


class FakeSession:
    def __init__(self, records=None):
        self.records = records if records is not None else []
        self.calls: list[tuple[str, dict]] = []

    def run(self, query, **parameters):
        self.calls.append((query, parameters))
        return self.records


def test_is_enabled_reflects_settings_flag(monkeypatch):
    monkeypatch.setattr("app.rag.graph_store.settings", Settings(graph_rag_enabled=False))
    assert is_enabled() is False

    monkeypatch.setattr("app.rag.graph_store.settings", Settings(graph_rag_enabled=True))
    assert is_enabled() is True


def test_ensure_constraints_runs_all_statements():
    session = FakeSession()
    ensure_constraints(session=session)
    assert len(session.calls) == 3
    assert all("CONSTRAINT" in query for query, _ in session.calls)


def test_upsert_incident_graph_without_interface_is_noop():
    session = FakeSession()
    upsert_incident_graph(
        incident_id="i1",
        description="incidente so texto, sem conector",
        interface_type=None,
        identifier=None,
        source_system=None,
        root_cause="causa qualquer",
        confidence=0.5,
        matched_document=None,
        session=session,
    )
    assert session.calls == []


def test_upsert_incident_graph_writes_expected_params():
    session = FakeSession()
    upsert_incident_graph(
        incident_id="i1",
        description="RFC destination indisponivel",
        interface_type="rfc",
        identifier="RFC-GWY-POOL-TIMEOUT-DEMO",
        source_system="RFC",
        root_cause="Pool de processos de dialogo esgotado",
        confidence=0.8,
        matched_document="rfc_gateway_pool_timeout.md",
        session=session,
    )
    assert len(session.calls) == 1
    query, params = session.calls[0]
    assert "MERGE (i:Incident" in query
    assert params["interface_type"] == "rfc"
    assert params["identifier"] == "RFC-GWY-POOL-TIMEOUT-DEMO"
    assert params["matched_document"] == "rfc_gateway_pool_timeout.md"


def test_graph_context_returns_empty_when_disabled(monkeypatch):
    monkeypatch.setattr("app.rag.graph_store.settings", Settings(graph_rag_enabled=False))
    session = FakeSession(
        records=[{"root_cause": "x", "source_system": "RFC", "matched_document": None}]
    )
    result = graph_context("rfc", "RFC-GWY-POOL-TIMEOUT-DEMO", session=session)
    assert result == []
    assert session.calls == []  # nem chega a consultar o Neo4j


def test_graph_context_returns_empty_without_identifier(monkeypatch):
    monkeypatch.setattr("app.rag.graph_store.settings", Settings(graph_rag_enabled=True))
    session = FakeSession()
    assert graph_context("rfc", None, session=session) == []
    assert graph_context(None, "RFC-GWY-POOL-TIMEOUT-DEMO", session=session) == []


def test_graph_context_maps_records_when_enabled(monkeypatch):
    monkeypatch.setattr("app.rag.graph_store.settings", Settings(graph_rag_enabled=True))
    session = FakeSession(
        records=[
            {
                "root_cause": "Pool de processos de dialogo esgotado",
                "source_system": "RFC",
                "matched_document": "rfc_gateway_pool_timeout.md",
                "evidence_strength": 0.8,
                "is_grounded": True,
            }
        ]
    )
    result = graph_context("rfc", "RFC-GWY-POOL-TIMEOUT-DEMO", session=session)

    assert len(result) == 1
    assert result[0].root_cause == "Pool de processos de dialogo esgotado"
    assert result[0].source_system == "RFC"
    assert result[0].matched_document == "rfc_gateway_pool_timeout.md"
    assert result[0].evidence_strength == 0.8
    assert result[0].is_grounded is True


def test_graph_context_filters_out_ungrounded_hypotheses_by_default(monkeypatch):
    """DA-16: um diagnostico anterior gravado com baixa evidencia nao
    deve voltar como 'historico' para alimentar o proximo prompt - so
    incidentes com is_grounded=True aparecem por padrao."""
    monkeypatch.setattr("app.rag.graph_store.settings", Settings(graph_rag_enabled=True))
    session = FakeSession(
        records=[
            {
                "root_cause": "hipotese fraca, nao confirmada",
                "source_system": "RFC",
                "matched_document": None,
                "evidence_strength": 0.2,
                "is_grounded": False,
            },
            {
                "root_cause": "causa confirmada",
                "source_system": "RFC",
                "matched_document": "doc.md",
                "evidence_strength": 0.9,
                "is_grounded": True,
            },
        ]
    )
    result = graph_context("rfc", "RFC-GWY-POOL-TIMEOUT-DEMO", session=session)
    assert len(result) == 1
    assert result[0].root_cause == "causa confirmada"

    result_all = graph_context(
        "rfc", "RFC-GWY-POOL-TIMEOUT-DEMO", include_ungrounded=True, session=session
    )
    assert len(result_all) == 2


def test_format_graph_context_for_prompt_empty():
    assert format_graph_context_for_prompt([]) == ""


def test_format_graph_context_for_prompt_with_history(monkeypatch):
    monkeypatch.setattr("app.rag.graph_store.settings", Settings(graph_rag_enabled=True))
    session = FakeSession(
        records=[
            {
                "root_cause": "causa X",
                "source_system": "RFC",
                "matched_document": "doc.md",
                "evidence_strength": 0.9,
                "is_grounded": True,
            }
        ]
    )
    related = graph_context("rfc", "ID-1", session=session)
    text = format_graph_context_for_prompt(related)

    assert "1 incidente" in text
    assert "causa X" in text
    assert "doc.md" in text
    assert "confirmada" in text


def test_format_graph_context_for_prompt_labels_ungrounded_hypothesis():
    related = graph_context.__globals__["RelatedIncident"](
        interface_identifier="ID-1",
        source_system="RFC",
        root_cause="hipotese fraca",
        matched_document=None,
        evidence_strength=0.1,
        is_grounded=False,
    )
    text = format_graph_context_for_prompt([related])
    assert "NAO CONFIRMADA" in text
    assert "nao trate como fato" in text.lower()
