"""Migration 019: sinal de escalonamento (DA-44) e custo do diagnostico
(DA-67) gravados no incidente e expostos pela Admin API."""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.admin.routes import _incident_out
from app.llm.budget import TaskBudget
from app.models import DiagnosisResponse, IncidentRequest
from app.services import incident_recorder
from app.services.incident_repository import Incident

_ESC = {
    "escalation": "curated_tier_weak",
    "should_escalate": True,
    "tier": "curated",
    "abstained": False,
    "top_evidence": 0.31,
    "connector_real": False,
}


def _row(escalation=None, budget=None):
    resp = DiagnosisResponse(
        probable_root_cause="token expirado",
        model_confidence=0.7,
        diagnosis_confidence=0.4,
        next_steps=[],
        report_markdown="",
        llm_provider_used="ollama",
        escalation=escalation,
    )
    return incident_recorder.build_incident_row(
        incident_id=str(uuid.uuid4()),
        request=IncidentRequest(description="interface OData com 401", interface_type="odata"),
        response=resp,
        final_state={},
        latency_ms=10,
        budget=budget,
    )


def test_row_records_escalation_and_spent_budget():
    budget = TaskBudget(task_id="t", total_usd=1.0, spent_usd=0.0123456789, calls=2)
    row = _row(_ESC, budget)
    assert row["should_escalate"] is True
    assert row["escalation_reason"] == "curated_tier_weak"
    assert row["cost_usd"] == 0.012346
    assert row["llm_calls"] == 2


def test_rule_engine_is_zero_cost_not_unknown():
    row = _row(
        {**_ESC, "should_escalate": False, "escalation": "grounded"},
        TaskBudget(task_id="t", total_usd=1.0),
    )
    assert (row["cost_usd"], row["llm_calls"], row["should_escalate"]) == (0.0, 0, False)


def test_without_budget_or_signal_stays_null():
    row = _row()
    assert row["cost_usd"] is None and row["llm_calls"] is None
    assert row["should_escalate"] is None and row["escalation_reason"] is None


def test_row_keys_are_model_columns():
    row = _row(_ESC, TaskBudget(task_id="t", total_usd=1.0))
    assert set(row) <= set(Incident.__table__.columns.keys())


def test_admin_out_exposes_provenance_escalation_and_cost():
    inc = SimpleNamespace(
        **{c: None for c in Incident.__table__.columns.keys()}  # noqa: SIM118,
    )
    inc.id = uuid.uuid4()
    inc.created_at = datetime.now(UTC)
    inc.llm_model, inc.prompt_version, inc.prompt_digest = "qwen3", "v7", "a" * 64
    inc.should_escalate, inc.escalation_reason = True, "no_context"
    inc.cost_usd, inc.llm_calls = 0.01, 1
    out = _incident_out(inc, {"match": "none"})
    for key in (
        "llm_model",
        "prompt_version",
        "prompt_digest",
        "should_escalate",
        "escalation_reason",
        "cost_usd",
        "llm_calls",
    ):
        assert key in out, key
    assert out["escalation_reason"] == "no_context"


def test_migration_019_chains_on_018_and_matches_model():
    mig = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/019_incidents_escalation_and_cost.py"
    ).read_text()
    assert 'down_revision: str | Sequence[str] | None = "018"' in mig
    for col in ("should_escalate", "escalation_reason", "cost_usd", "llm_calls"):
        assert f'"{col}"' in mig and col in Incident.__table__.columns
