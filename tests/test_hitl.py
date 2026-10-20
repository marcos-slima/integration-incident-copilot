"""DA-75 (HITL) e DA-76/DA-83 (campos de resposta) — achados 001/008/015.

Cobre o caminho critico da auditoria:
- o escalonamento e' decidido ANTES de qualquer chamada ao LLM;
- o hitl_request fica no TOPO do update do node (nao aninhado em diagnosis);
- com checkpointer, o grafo pausa em interrupt() e retoma com Command(resume=...);
- POST /incidents/{id}/review retoma o checkpoint sem reexecutar connector/RAG/LLM;
- DiagnosisResponse carrega ai_warning e explanation.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Interrupt

import app.agent.graph as graph_module
import app.main as main_module
from app.agent import nodes
from app.agent.state import CopilotState
from app.models import IncidentRequest

WEAK_STATE: dict[str, Any] = {
    "description": "algo estranho aconteceu",
    "locale": "pt-BR",
    "llm_model": "fake-model",
    # curated + rerank fraco: compute_escalation_signal -> curated_tier_weak
    "retrieved_context": [
        {
            "source": "doc.md",
            "text": "trecho",
            "score": 0.1,
            "rerank_score_calibrated": 0.1,
            "collection": "sap_incident_docs",
        }
    ],
    "connector_data": None,
}


def _no_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nodes, "match_known_error", lambda *a, **k: None)


def _fail_if_llm_called(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []

    def _fail(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        raise AssertionError("invoke_via_gateway nao pode ser chamado quando ha escalonamento")

    monkeypatch.setattr(nodes, "invoke_via_gateway", _fail)
    return calls


def _mini_graph():
    graph = StateGraph(CopilotState)
    graph.add_node("diagnose", nodes.sap_diagnosis_node)
    graph.add_node("report", nodes.report_node)
    graph.add_edge(START, "diagnose")
    graph.add_edge("diagnose", "report")
    graph.add_edge("report", END)
    return graph.compile(checkpointer=MemorySaver())


# ---------------------------------------------------------------------------
# 001: escalonamento antes do LLM + hitl_request no topo
# ---------------------------------------------------------------------------


def test_escalation_happens_before_llm_and_hitl_request_is_top_level(monkeypatch):
    _no_rule(monkeypatch)
    calls = _fail_if_llm_called(monkeypatch)

    out = nodes.sap_diagnosis_node(dict(WEAK_STATE))

    assert calls == []
    assert out["hitl_request"]["escalation"]["should_escalate"] is True
    assert out["hitl_request"]["escalation"]["escalation"] == "curated_tier_weak"
    assert out["diagnosis"]["diagnosis_confidence"] == 0.0
    assert out["diagnosis"]["model_confidence"] == 0.0
    assert "revisao humana" in out["diagnosis"]["probable_root_cause"]
    assert out["diagnosis"]["matched_source"] == "doc.md"


def test_report_node_renders_pending_only_without_human_response():
    pending = nodes.report_node(
        {
            **WEAK_STATE,
            "hitl_request": {"incident_id": "i1"},
        }
    )
    assert "pendente de revisao humana" in pending["report_markdown"]

    final = nodes.report_node(
        {
            **WEAK_STATE,
            "diagnosis": {
                "probable_root_cause": "causa humana",
                "next_steps": [],
                "model_confidence": 0.0,
                "diagnosis_confidence": 0.0,
                "evidence_strength": 0.1,
            },
            "hitl_request": {"incident_id": "i1"},
            "hitl_response": {"action": "edit", "edited_cause": "causa humana"},
        }
    )
    assert "## Diagnostico do Incidente" in final["report_markdown"]
    assert "causa humana" in final["report_markdown"]


# ---------------------------------------------------------------------------
# 015: interrupt nativo + Command(resume=...)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("approve", "Diagnostico automatico suspenso"),
        ("edit", "causa revisada pelo operador"),
        ("reject", "rejeitado na revisao humana"),
    ],
)
def test_graph_interrupt_and_resume_without_llm(monkeypatch, action, expected):
    _no_rule(monkeypatch)
    calls = _fail_if_llm_called(monkeypatch)

    graph = _mini_graph()
    config = {"configurable": {"thread_id": f"hitl-{action}"}}
    first = graph.invoke({**WEAK_STATE, "hitl_interrupt_enabled": True}, config)

    # O node nao retornou: o pedido de revisao esta no interrupt, nao no state.
    assert "__interrupt__" in first
    snapshot = graph.get_state(config)
    assert len(snapshot.interrupts) == 1
    assert snapshot.interrupts[0].value["escalation"]["should_escalate"] is True
    assert calls == []

    final = graph.invoke(
        Command(
            resume={
                "action": action,
                "edited_cause": "causa revisada pelo operador" if action == "edit" else None,
                "reviewed_by": "human",
            }
        ),
        config,
    )

    assert calls == []
    assert expected in final["diagnosis"]["probable_root_cause"]
    assert final["hitl_response"]["action"] == action
    assert final["hitl_reviewed"] is True
    assert final["report_markdown"]


def test_review_endpoint_resumes_interrupt(monkeypatch):
    _no_rule(monkeypatch)
    _fail_if_llm_called(monkeypatch)

    graph = _mini_graph()
    thread_id = "incident-review-1"
    config = {"configurable": {"thread_id": thread_id}}
    graph.invoke({**WEAK_STATE, "hitl_interrupt_enabled": True}, config)
    assert graph.get_state(config).interrupts

    monkeypatch.setattr(main_module.settings, "checkpointing_enabled", True)
    monkeypatch.setattr(graph_module, "get_graph", lambda: graph)

    client = TestClient(main_module.app)
    response = client.post(
        f"/incidents/{thread_id}/review",
        json={"action": "edit", "edit": "causa humana", "reviewed_by": "human"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "reviewed"

    # Ja revisado: nao ha mais interrupt pendente.
    again = client.post(f"/incidents/{thread_id}/review", json={"action": "approve"})
    assert again.status_code == 400, again.text

    unknown = client.post("/incidents/nao-existe/review", json={"action": "approve"})
    assert unknown.status_code == 404, unknown.text

    invalid = client.post(f"/incidents/{thread_id}/review", json={"action": "edit", "edit": ""})
    assert invalid.status_code == 422, invalid.text

    assert graph.get_state(config).values["diagnosis"]["probable_root_cause"] == "causa humana"


def test_review_endpoint_requires_checkpointing(monkeypatch):
    monkeypatch.setattr(main_module.settings, "checkpointing_enabled", False)

    client = TestClient(main_module.app)
    response = client.post("/incidents/qualquer/review", json={"action": "approve"})

    assert response.status_code == 400
    assert "CHECKPOINTING_ENABLED" in response.text


# ---------------------------------------------------------------------------
# 008 / DA-76 / DA-83: campos de resposta
# ---------------------------------------------------------------------------


def _mock_run_diagnosis_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph_module, "record_incident", lambda **kwargs: None)
    import app.metrics as metrics_module

    monkeypatch.setattr(metrics_module, "observe_diagnosis", lambda **kwargs: None)


def test_run_diagnosis_maps_interrupt_to_pending_response(monkeypatch):
    _mock_run_diagnosis_deps(monkeypatch)
    payload = {
        "probable_root_cause": "Diagnostico pendente de revisao humana.",
        "matched_source": "doc.md",
        "evidence_strength": 0.1,
        "escalation": {"should_escalate": True, "escalation": "curated_tier_weak"},
    }
    monkeypatch.setattr(
        graph_module,
        "_invoke_graph_with_timeout",
        lambda *a, **k: {
            "__interrupt__": [Interrupt(value=payload)],
            "locale": "pt-BR",
            "llm_model": "fake-model",
            "description": "x",
        },
    )

    response = graph_module.run_diagnosis(IncidentRequest(description="x"))

    assert response.probable_root_cause == "Diagnostico pendente de revisao humana."
    assert response.diagnosis_confidence == 0.0
    assert "pendente de revisao humana" in response.report_markdown
    assert response.ai_warning
    assert response.prompt_digest is None


def test_run_diagnosis_maps_explanation_and_ai_warning(monkeypatch):
    _mock_run_diagnosis_deps(monkeypatch)
    monkeypatch.setattr(
        graph_module,
        "_invoke_graph_with_timeout",
        lambda *a, **k: {
            "diagnosis": {
                "probable_root_cause": "causa",
                "model_confidence": 0.8,
                "diagnosis_confidence": 0.7,
                "next_steps": [],
            },
            "report_markdown": "relatorio",
            "explanation": "baseado no documento X",
            "locale": "pt-BR",
            "description": "x",
        },
    )

    response = graph_module.run_diagnosis(IncidentRequest(description="x"))

    assert response.explanation == "baseado no documento X"
    assert response.ai_warning.startswith("Aviso de IA")
    assert response.diagnosis_confidence == 0.7
