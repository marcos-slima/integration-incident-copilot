"""DA-93: dominio fiscal DF-e, fase 1 - flag e contrato FiscalContext."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.agent import graph
from app.config import Settings
from app.models import FiscalContext, IncidentRequest

_CTX = {"environment": "homologation", "jurisdiction": "SP"}


def _initial_state(monkeypatch: pytest.MonkeyPatch, enabled: bool, **req: Any) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake_invoke(initial_state, timeout_seconds=None, thread_id=None):
        seen.update(initial_state)
        return {**initial_state, "diagnosis": {"probable_root_cause": "x", "confidence": 0.5}}

    monkeypatch.setattr(graph.settings, "fiscal_domain_enabled", enabled)
    monkeypatch.setattr(graph, "_invoke_graph_with_timeout", fake_invoke)
    monkeypatch.setattr(graph, "record_incident", lambda *a, **k: None)
    graph.run_diagnosis(IncidentRequest(description="NF-e rejeitada cStat 204", **req))
    return seen


def test_flag_desligada_por_padrao() -> None:
    assert Settings().fiscal_domain_enabled is False


def test_recorte_do_mvp_aceito() -> None:
    ctx = FiscalContext(**_CTX)
    assert (ctx.family, ctx.model, ctx.layout_version, ctx.schema_version) == (
        "nfe",
        "55",
        "4.00",
        "1",
    )


@pytest.mark.parametrize(
    "override",
    [
        {"jurisdiction": "XX"},
        {"jurisdiction": "sp"},
        {"model": "65"},
        {"layout_version": "3.10"},
        {"environment": "staging"},
    ],
)
def test_fora_do_recorte_rejeitado(override: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        FiscalContext(**{**_CTX, **override})


def test_flag_ligada_propaga_contexto(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _initial_state(monkeypatch, True, fiscal_context=_CTX)
    assert seen["fiscal_context"]["jurisdiction"] == "SP"
    assert seen["fiscal_context"]["environment"] == "homologation"


def test_flag_desligada_descarta_contexto(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _initial_state(monkeypatch, False, fiscal_context=_CTX)
    assert seen["fiscal_context"] is None


def test_sem_contexto_nao_inventa(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _initial_state(monkeypatch, True)["fiscal_context"] is None
