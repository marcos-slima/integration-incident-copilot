"""DA-21: graph_enrich_node/graph_write_node nao podem derrubar o
diagnostico inteiro so porque o Neo4j esta temporariamente
indisponivel - devem degradar graciosamente (historico vazio / escrita
pulada) e apenas logar um warning. Um erro que sinaliza bug NOSSO
(Cypher/schema invalido) deve continuar propagando normalmente - mesmo
principio de separar falha de infraestrutura de erro de aplicacao usado
no Hybrid Inference (DA-20, tests/test_llm_factory.py)."""

import pytest
from neo4j.exceptions import CypherSyntaxError, DriverError, ServiceUnavailable, TransientError

from app.agent.nodes import graph_enrich_node, graph_write_node


def test_graph_enrich_node_degrades_gracefully_on_driver_error(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise ServiceUnavailable("neo4j indisponivel")

    monkeypatch.setattr("app.agent.nodes.graph_context", _boom)
    result = graph_enrich_node({"interface_type": "rfc", "identifier": "ID-1"})
    assert result == {"graph_history": []}


def test_graph_enrich_node_degrades_gracefully_on_transient_error(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise TransientError("tente de novo depois")

    monkeypatch.setattr("app.agent.nodes.graph_context", _boom)
    result = graph_enrich_node({"interface_type": "rfc", "identifier": "ID-1"})
    assert result == {"graph_history": []}


def test_graph_enrich_node_propagates_non_transport_errors(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise CypherSyntaxError("query invalida - bug nosso")

    monkeypatch.setattr("app.agent.nodes.graph_context", _boom)
    with pytest.raises(CypherSyntaxError):
        graph_enrich_node({"interface_type": "rfc", "identifier": "ID-1"})


def test_graph_write_node_degrades_gracefully_on_driver_error(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise DriverError("conexao recusada")

    monkeypatch.setattr("app.agent.nodes.upsert_incident_graph", _boom)
    result = graph_write_node(
        {
            "description": "incidente qualquer",
            "diagnosis": {"probable_root_cause": "causa", "confidence": 0.5},
            "connector_data": None,
        }
    )
    assert result == {}


def test_graph_write_node_propagates_non_transport_errors(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise CypherSyntaxError("query invalida - bug nosso")

    monkeypatch.setattr("app.agent.nodes.upsert_incident_graph", _boom)
    with pytest.raises(CypherSyntaxError):
        graph_write_node(
            {
                "description": "incidente qualquer",
                "diagnosis": {"probable_root_cause": "causa", "confidence": 0.5},
                "connector_data": None,
            }
        )


def test_graph_write_node_uses_incident_id_from_state(monkeypatch):
    """DA-28: o incident_id gerado em graph.py::run_diagnosis (e
    devolvido em DiagnosisResponse.incident_id) tem que ser o mesmo
    gravado no Neo4j - senao verify_incident() nunca acha o incidente
    de volta pelo id que o caller recebeu."""
    captured = {}

    def _capture(*_args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("app.agent.nodes.upsert_incident_graph", _capture)
    graph_write_node(
        {
            "description": "incidente qualquer",
            "diagnosis": {"probable_root_cause": "causa", "confidence": 0.5},
            "connector_data": None,
            "incident_id": "id-fixo-do-state",
        }
    )
    assert captured["incident_id"] == "id-fixo-do-state"


def test_graph_write_node_falls_back_to_random_id_when_state_lacks_one(monkeypatch):
    """Seguranca para chamada direta a graph_write_node sem passar por
    run_diagnosis (ex: um teste antigo, ou um caller futuro que nao
    popule incident_id) - nao deve quebrar, so gerar um id novo."""
    captured = {}

    def _capture(*_args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("app.agent.nodes.upsert_incident_graph", _capture)
    graph_write_node(
        {
            "description": "incidente qualquer",
            "diagnosis": {"probable_root_cause": "causa", "confidence": 0.5},
            "connector_data": None,
        }
    )
    assert captured["incident_id"]  # algum uuid gerado, nao vazio/None
