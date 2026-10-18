"""DA-67: orcamento por diagnostico (tarefa), reserva, degradacao e ledger.

Sem infraestrutura: nenhum provider real e chamado (build_and_invoke e
fake) e Settings(_env_file=None) evita que o .env local ligue metering
ou banco.
"""

import pytest

from app import circuit_breaker as cb_module
from app.admin.repository import record_budget_ledger
from app.config import Settings
from app.llm import budget as budget_mod
from app.llm.budget import (
    BudgetExhaustedError,
    TaskBudget,
    close_task_budget,
    get_task_budget,
    open_task_budget,
)
from app.llm.failures import FAILURE_POLICIES, FailureClass, classify_failure
from app.llm.gateway import (
    PolicyViolationError,
    _estimate_tokens,
    circuit_breaker,
    invoke_via_gateway,
)
from app.llm.routing import _estimate_cost_usd as routing_cost

PROMPT = "erro 401 no iFlow " * 200  # alguns milhares de caracteres
TASK = "inc-da67-test"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    circuit_breaker.reset()
    cb_module._redis_available = None
    monkeypatch.setattr(budget_mod, "_budgets", {})
    # dado publico: deixa o cloud elegivel (B-04 usa 'confidential' por default)
    monkeypatch.setattr("app.llm.gateway.settings.sensitivity_default", "public")
    yield
    circuit_breaker.reset()
    cb_module._redis_available = None


def _cfg(**kw) -> Settings:
    base = {
        "llm_provider": "openai",
        "openai_api_key": "fake-test-value",
        "llm_fallback_provider": "",
        "llm_gateway_max_cost_usd": 10.0,
        "llm_budget_reserve_ratio": 0.25,
        "metering_enabled": False,
        "database_url": "",
    }
    base.update(kw)
    return Settings(_env_file=None, **base)


def _openai_cost(cfg: Settings) -> float:
    return routing_cost("openai", _estimate_tokens(PROMPT), cfg)


# ---------------------------------------------------------------------
# TaskBudget / registro
# ---------------------------------------------------------------------


def test_affordable_discounts_reserve():
    b = TaskBudget(task_id="t", total_usd=1.25)
    assert b.affordable_usd(0.25) == pytest.approx(1.0)
    assert b.affordable_usd(0.0) == pytest.approx(1.25)


def test_charge_accumulates_and_never_goes_negative():
    b = TaskBudget(task_id="t", total_usd=1.0)
    b.charge(0.4)
    b.charge(0.4)
    assert b.spent_usd == pytest.approx(0.8)
    assert b.calls == 2
    b.charge(0.5)
    assert b.remaining_usd == 0.0
    with pytest.raises(ValueError):
        b.charge(-0.1)


def test_registry_open_get_close():
    open_task_budget(TASK, 2.0)
    assert get_task_budget(TASK).total_usd == 2.0
    assert get_task_budget(None) is None
    closed = close_task_budget(TASK)
    assert closed is not None and get_task_budget(TASK) is None


# ---------------------------------------------------------------------
# Gateway: orcamento acumulado por diagnostico
# ---------------------------------------------------------------------


def test_spend_is_charged_and_exhaustion_blocks_next_call():
    cfg = _cfg()
    cost = _openai_cost(cfg)
    assert cost > 0
    # cabe UMA chamada com reserva, nao duas
    open_task_budget(TASK, cost * 1.25 * 1.5)
    calls = []

    def ok(llm):
        calls.append(1)
        return {"messages": []}

    state = {"description": "x", "incident_id": TASK}
    invoke_via_gateway(ok, state=state, prompt_text=PROMPT, config=cfg)
    assert get_task_budget(TASK).spent_usd == pytest.approx(cost)

    with pytest.raises(BudgetExhaustedError, match="budget do diagnostico esgotado"):
        invoke_via_gateway(ok, state=state, prompt_text=PROMPT, config=cfg)
    assert len(calls) == 1, "a segunda chamada nao pode chegar ao provider"


@pytest.mark.parametrize(
    ("factor", "allowed"),
    [(1.10, False), (1.24, False), (1.26, True), (2.0, True)],
)
def test_reserve_is_applied_before_accepting_candidate(factor, allowed):
    """custo_estimado * (1 + 0.25) precisa caber no restante."""
    cfg = _cfg()
    cost = _openai_cost(cfg)
    open_task_budget(TASK, cost * factor)
    state = {"description": "x", "incident_id": TASK}

    if allowed:
        invoke_via_gateway(lambda llm: {"ok": 1}, state=state, prompt_text=PROMPT, config=cfg)
    else:
        with pytest.raises(BudgetExhaustedError):
            invoke_via_gateway(lambda llm: {"ok": 1}, state=state, prompt_text=PROMPT, config=cfg)


def test_degrades_to_cheaper_candidate_when_budget_is_short(caplog):
    """Cloud fica fora do orcamento restante; o local (custo 0) ainda cabe."""
    cfg = _cfg(llm_fallback_provider="ollama", ollama_host="http://127.0.0.1:11434")
    open_task_budget(TASK, _openai_cost(cfg) * 0.5)
    used = []

    def ok(llm):
        used.append(type(llm).__name__)
        return {"ok": 1}

    with caplog.at_level("INFO", logger="app.llm.gateway"):
        _, provider = invoke_via_gateway(
            ok, state={"description": "x", "incident_id": TASK}, prompt_text=PROMPT, config=cfg
        )

    assert provider == "ollama"
    assert used == ["ChatOllama"]
    decision_logs = [r.getMessage() for r in caplog.records if "routing decision" in r.getMessage()]
    assert decision_logs and "orcamento_excedido" in decision_logs[-1]


def test_without_task_budget_only_per_call_cap_applies():
    """Fora de run_diagnosis: comportamento anterior (teto por chamada,
    PolicyViolationError - nao BudgetExhaustedError)."""
    cfg = _cfg(llm_gateway_max_cost_usd=0.000001)
    with pytest.raises(PolicyViolationError) as ei:
        invoke_via_gateway(
            lambda llm: {"ok": 1}, state={"description": "x"}, prompt_text=PROMPT, config=cfg
        )
    assert not isinstance(ei.value, BudgetExhaustedError)


def test_per_call_cap_still_wins_when_smaller_than_task_budget():
    cfg = _cfg(llm_gateway_max_cost_usd=0.000001)
    open_task_budget(TASK, 100.0)
    with pytest.raises(PolicyViolationError):
        invoke_via_gateway(
            lambda llm: {"ok": 1},
            state={"description": "x", "incident_id": TASK},
            prompt_text=PROMPT,
            config=cfg,
        )


def test_failed_call_is_not_charged():
    cfg = _cfg()
    open_task_budget(TASK, 100.0)

    def fail(llm):
        raise ConnectionError("down")

    with pytest.raises(Exception):  # noqa: B017 - qualquer erro final do gateway
        invoke_via_gateway(
            fail, state={"description": "x", "incident_id": TASK}, prompt_text=PROMPT, config=cfg
        )
    assert get_task_budget(TASK).spent_usd == 0.0


# ---------------------------------------------------------------------
# DA-68: classe da falha
# ---------------------------------------------------------------------


def test_budget_exhausted_maps_to_failure_class_without_failover():
    exc = BudgetExhaustedError("budget do diagnostico esgotado")
    assert classify_failure(exc) is FailureClass.BUDGET_EXHAUSTED
    policy = FAILURE_POLICIES[FailureClass.BUDGET_EXHAUSTED]
    assert policy.retry is False and policy.failover is False


# ---------------------------------------------------------------------
# run_diagnosis abre e fecha o orcamento
# ---------------------------------------------------------------------


def test_run_diagnosis_opens_and_closes_budget_even_on_error(monkeypatch):
    from app.agent import graph
    from app.models import IncidentRequest

    seen = {}

    def fake_invoke(initial_state, timeout_seconds=None, thread_id=None):
        seen["budget"] = get_task_budget(initial_state["incident_id"])
        seen["timeout_seconds"] = timeout_seconds
        seen["thread_id"] = thread_id
        raise RuntimeError("falha no grafo")

    monkeypatch.setattr(graph, "_invoke_graph_with_timeout", fake_invoke)
    monkeypatch.setattr(graph.settings, "diagnosis_budget_usd", 0.75)

    with pytest.raises(RuntimeError):
        graph.run_diagnosis(IncidentRequest(description="OData 401 apos refresh de token"))

    assert seen["budget"] is not None and seen["budget"].total_usd == 0.75
    assert seen["timeout_seconds"] is None  # thread_id nao vaza para timeout_seconds
    assert budget_mod._budgets == {}, "orcamento precisa ser fechado mesmo com erro"


# ---------------------------------------------------------------------
# Ledger persistente (best-effort)
# ---------------------------------------------------------------------


def test_record_budget_ledger_noop_sem_banco(monkeypatch):
    monkeypatch.setattr("app.config.settings.database_url", "")
    assert (
        record_budget_ledger(
            incident_id=TASK,
            origin="ollama",
            model_id="qwen3.6:35b-a3b",
            estimated_cost_usd=0.01,
            actual_cost_usd=0.009,
            tokens_estimated=1000,
            tokens_actual_in=950,
            tokens_actual_out=0,
        )
        is False
    )
