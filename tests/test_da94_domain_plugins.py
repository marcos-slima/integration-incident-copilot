"""DA-94: ponto de extensao de produto de dominio no core.

O core nao conhece nenhum dominio: estes testes usam um plugin falso.
O plugin real do DF-e e' testado em packages/dfe/tests.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from app.agent import domain_plugins, graph
from app.agent.domain_plugins import (
    DomainContextError,
    DomainPluginError,
    load_plugins,
    make_domain_tools_node,
    resolve_context,
)
from app.config import Settings
from app.models import IncidentRequest


class _Fake:
    name = "fake"

    def validate_context(self, context: dict[str, Any]) -> dict[str, Any] | None:
        if context.get("kind") != "fake":
            return None
        if "bad" in context:
            raise ValueError("campo invalido")
        return {"kind": "fake", "ok": True}

    def run(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        if state.get("payload") == "boom":
            raise RuntimeError("falhou")
        return [{"id": "F1", "kind": "fact", "source": "tool", "statement": "fato"}]

    def context_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"kind": {"const": "fake"}}}


@pytest.fixture
def fake_module(monkeypatch: pytest.MonkeyPatch) -> str:
    mod = types.ModuleType("fake_domain_plugin")
    mod.PLUGIN = _Fake()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_domain_plugin", mod)
    load_plugins.cache_clear()
    yield "fake_domain_plugin"
    load_plugins.cache_clear()


def test_default_sem_plugins() -> None:
    assert Settings().domain_plugins == ""
    assert load_plugins("") == ()


def test_plugin_inexistente_falha_fechado() -> None:
    load_plugins.cache_clear()
    with pytest.raises(DomainPluginError):
        load_plugins("modulo.que.nao.existe")


def test_sem_plugin_contexto_descartado() -> None:
    assert resolve_context((), {"kind": "fake"}) == (None, None)


def test_contexto_aceito_e_recusado(fake_module: str) -> None:
    plugins = load_plugins(fake_module)
    assert resolve_context(plugins, {"kind": "fake"}) == ("fake", {"kind": "fake", "ok": True})
    with pytest.raises(DomainContextError):
        resolve_context(plugins, {"kind": "fake", "bad": 1})
    with pytest.raises(DomainContextError):
        resolve_context(plugins, {"kind": "outro"})


def test_no_domain_tools_isola_falha(fake_module: str) -> None:
    node = make_domain_tools_node(load_plugins(fake_module))
    assert node({"domain_plugin": "fake"})["domain_findings"][0]["id"] == "F1"
    assert (
        node({"domain_plugin": "fake", "payload": "boom"})["domain_findings"][0]["id"] == "G-plugin"
    )
    assert node({"domain_plugin": None}) == {"domain_findings": []}


def _edges(compiled) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in compiled.get_graph().edges}


def test_grafo_do_core_inalterado_sem_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph.settings, "domain_plugins", "")
    edges = _edges(graph.build_graph())
    assert ("connector", "retrieve") in edges
    assert not any("domain_tools" in e for e in edges)


def test_grafo_com_plugin_roda_antes_do_retrieve(
    monkeypatch: pytest.MonkeyPatch, fake_module: str
) -> None:
    monkeypatch.setattr(graph.settings, "domain_plugins", fake_module)
    edges = _edges(graph.build_graph())
    assert ("connector", "domain_tools") in edges and ("domain_tools", "retrieve") in edges


def test_run_diagnosis_propaga_contexto(monkeypatch: pytest.MonkeyPatch, fake_module: str) -> None:
    seen: dict[str, Any] = {}

    def fake_invoke(initial_state, timeout_seconds=None, thread_id=None):
        seen.update(initial_state)
        return {**initial_state, "diagnosis": {"probable_root_cause": "x", "confidence": 0.5},
                "domain_findings": [{"id": "F1", "kind": "fact", "source": "tool", "statement": "fato"}]}  # fmt: skip

    monkeypatch.setattr(graph.settings, "domain_plugins", fake_module)
    monkeypatch.setattr(graph, "_invoke_graph_with_timeout", fake_invoke)
    monkeypatch.setattr(graph, "record_incident", lambda *a, **k: None)
    r = graph.run_diagnosis(IncidentRequest(description="d", domain_context={"kind": "fake"}))
    assert seen["domain_plugin"] == "fake" and seen["domain_context"]["ok"] is True
    assert [f.id for f in r.findings] == ["F1", "H-diagnosis"]


def test_modulo_nao_importa_produto() -> None:
    src = Path(domain_plugins.__file__).read_text(encoding="utf-8")
    assert "dfe_copilot" not in src
