"""DA-93 fase 4: findings (fato x hipotese), cause_status e rule_reference."""

from __future__ import annotations

from typing import Any

import pytest

from app.agent import graph
from app.agent.findings import build_findings
from app.models import DiagnosisResponse, Evidence, IncidentRequest


def test_causa_do_llm_e_hipotese() -> None:
    f, status = build_findings(
        {"probable_root_cause": "Token expirado", "llm_provider_used": "ollama"}
    )
    assert [(x["kind"], x["source"]) for x in f] == [("hypothesis", "llm")]
    assert status == "hypothesis"


def test_causa_do_rule_engine_e_hipotese_de_regra() -> None:
    f, _ = build_findings(
        {
            "probable_root_cause": "IDoc 51",
            "llm_provider_used": "rule_engine",
            "matched_source": "rule_engine:x",
        }
    )
    assert f[0]["source"] == "rule_reference" and f[0]["evidence_refs"] == ["rule_engine:x"]


def test_fato_sem_fonte_deterministica_e_rebaixado() -> None:
    f, _ = build_findings({}, [{"id": "X", "kind": "fact", "source": "llm", "statement": "s"}])
    assert f[0]["kind"] == "hypothesis"


def test_fato_de_ferramenta_mantido() -> None:
    f, status = build_findings(
        {}, [{"id": "F", "kind": "fact", "source": "tool", "statement": "cStat 204"}]
    )
    assert f[0]["kind"] == "fact" and status == "inconclusive"


@pytest.mark.parametrize("cause", ["", "N/A", "   "])
def test_sem_causa_inconclusivo(cause: str) -> None:
    assert build_findings({"probable_root_cause": cause}) == ([], "inconclusive")


def test_pipeline_nunca_confirma(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_invoke(initial_state, timeout_seconds=None, thread_id=None):
        return {**initial_state, "diagnosis": {"probable_root_cause": "causa", "confidence": 0.5}}

    monkeypatch.setattr(graph, "_invoke_graph_with_timeout", fake_invoke)
    monkeypatch.setattr(graph, "record_incident", lambda *a, **k: None)
    r: Any = graph.run_diagnosis(IncidentRequest(description="OData 401"))
    assert r.cause_status == "hypothesis"
    assert all(x.kind == "hypothesis" for x in r.findings)


def test_rule_reference_e_trust_level_valido() -> None:
    Evidence(
        source_id="rule_engine:x",
        source_type="rule_engine",
        excerpt="e",
        trust_level="rule_reference",
    )


def test_confirmed_nao_e_default() -> None:
    assert DiagnosisResponse.model_fields["cause_status"].default == "inconclusive"
