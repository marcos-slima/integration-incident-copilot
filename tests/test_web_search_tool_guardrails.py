"""Avaliacao externa (nova revisao, P1 - "Agente ReAct pode vazar
dados na web"): dois guardrails de codigo, nao so de prompt.

1. web_search_tool (app/agent/nodes.py::_make_web_search_tool) aplica
   redact_pii_text() na query que o LLM monta ANTES dela sair para o
   DuckDuckGo - a instrucao "nunca dados sensiveis" no docstring do
   tool e so uma instrucao de prompt, nao um enforcement.
2. create_react_agent.invoke() agora recebe recursion_limit=
   settings.react_agent_recursion_limit em vez de rodar sem limite
   explicito (default do LangGraph e 25 "super-steps", generoso demais
   para um agente de um unico tool).
"""

from typing import ClassVar

from app.agent.nodes import _make_web_search_tool, _run_diagnosis_agent
from app.services.web_search_sources import ApprovedSource


def _approve(monkeypatch, *interface_types: str) -> None:
    """DA-57: registra fonte(s) aprovada(s) como se viessem do banco.

    `nodes.py` importa `resolve_approved_source` de dentro da funcao, entao
    monkeypatchar o atributo no modulo de origem vale para os dois
    consumidores (node e tool ReAct).
    """
    wanted = set(interface_types)
    monkeypatch.setattr(
        "app.services.web_search_sources.resolve_approved_source",
        lambda it: (
            ApprovedSource(
                interface_type=it,
                site_filter=f"site:exemplo-{it}.com",
                tech_term=f"termo {it}",
            )
            if it in wanted
            else None
        ),
    )


def _no_sources(monkeypatch) -> None:
    """DA-57 fail-closed: nenhuma linha habilitada na tabela."""
    monkeypatch.setattr("app.services.web_search_sources.resolve_approved_source", lambda it: None)


class _FakeDDGS:
    """Substitui ddgs.DDGS - grava a query recebida (para provar o que
    de fato saiu para a rede) e devolve resultados fixos, sem tocar a
    rede de verdade."""

    captured_queries: ClassVar[list[str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def text(self, query, max_results=5):
        _FakeDDGS.captured_queries.append(query)
        return [{"title": "t", "href": "https://example.com", "body": "b"}]


def test_web_search_tool_redacts_pii_from_llm_composed_query(monkeypatch):
    _FakeDDGS.captured_queries = []
    monkeypatch.setattr("app.agent.nodes.DDGS", _FakeDDGS)
    _approve(monkeypatch, "odata")

    tool = _make_web_search_tool({"interface_type": "odata"})
    tool.invoke({"query": "erro no IDoc 1234567890123456 relatado por joao@empresa.com"})

    assert len(_FakeDDGS.captured_queries) == 1
    sent_query = _FakeDDGS.captured_queries[0]
    assert "1234567890123456" not in sent_query
    assert "joao@empresa.com" not in sent_query
    assert "[IDOC_REDACTED]" in sent_query or "[EMAIL_REDACTED]" in sent_query


def test_web_search_tool_passes_through_generic_technical_query(monkeypatch):
    _FakeDDGS.captured_queries = []
    monkeypatch.setattr("app.agent.nodes.DDGS", _FakeDDGS)
    _approve(monkeypatch, "odata")

    tool = _make_web_search_tool({"interface_type": "odata"})
    tool.invoke({"query": "BAPI_MATERIAL_SAVEDATA authorization error"})

    assert "BAPI_MATERIAL_SAVEDATA authorization error" in _FakeDDGS.captured_queries[0]


def test_web_search_tool_uses_site_filter_from_approved_source(monkeypatch):
    """DA-57: o filtro de site vem da LINHA, nao de um mapa em codigo."""
    _FakeDDGS.captured_queries = []
    monkeypatch.setattr("app.agent.nodes.DDGS", _FakeDDGS)
    _approve(monkeypatch, "po")

    _make_web_search_tool({"interface_type": "po"}).invoke({"query": "IDoc status 51"})

    assert "site:exemplo-po.com" in _FakeDDGS.captured_queries[0]


def test_web_search_tool_disabled_when_no_approved_source(monkeypatch):
    """DA-57 fail-closed: sem linha habilitada, a tool nao busca — e diz
    que nao ha fonte, em vez de cair num filtro generico silencioso."""
    _FakeDDGS.captured_queries = []
    monkeypatch.setattr("app.agent.nodes.DDGS", _FakeDDGS)
    _no_sources(monkeypatch)

    tool = _make_web_search_tool({"interface_type": "po"})
    said = tool.invoke({"query": "IDoc status 51"})

    assert _FakeDDGS.captured_queries == []
    assert "fonte aprovada" in said


def test_web_search_tool_works_for_successfactors_and_po(monkeypatch):
    """DA-57: `successfactors` (DA-34) e `po` (DA-56) NAO estavam em nenhum
    dos dois mapas hardcoded e perdiam tambem o tech_term. Aqui proves que a
    resolucao funciona para os dois — quem guarantees a linha e' a migration
    008 / o cadastro no admin, nao um mapa em codigo."""
    for interface_type in ("successfactors", "po"):
        _FakeDDGS.captured_queries = []
        monkeypatch.setattr("app.agent.nodes.DDGS", _FakeDDGS)
        _approve(monkeypatch, interface_type)

        _make_web_search_tool({"interface_type": interface_type}).invoke({"query": "erro"})
        assert len(_FakeDDGS.captured_queries) == 1
        assert f"site:exemplo-{interface_type}.com" in _FakeDDGS.captured_queries[0]


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeReactAgent:
    """Substitui o objeto devolvido por create_react_agent - so grava
    o `config` recebido em .invoke() para provar que recursion_limit
    foi passado, sem rodar nenhum LLM/tool de verdade."""

    captured_configs: ClassVar[list[dict]] = []

    def invoke(self, messages, config=None):
        _FakeReactAgent.captured_configs.append(config)
        return {
            "messages": [
                _FakeMessage(
                    '{"matched_source": null, "probable_root_cause": "x", '
                    '"confidence": 0.5, "next_steps": ["passo 1"]}'
                )
            ]
        }


def _fake_invoke_via_gateway(build_and_invoke, state=None, prompt_text="", model_name=None):
    # Simula o AI Gateway chamando o closure com um "llm" qualquer -
    # o que importa aqui e que build_and_invoke() de fato roda, para
    # exercitar o config passado a create_react_agent(...).invoke().
    return build_and_invoke(llm=object()), "ollama"


def test_run_diagnosis_agent_sets_recursion_limit_on_react_agent_invoke(monkeypatch):
    _FakeReactAgent.captured_configs = []
    monkeypatch.setattr("app.agent.nodes.create_react_agent", lambda *a, **k: _FakeReactAgent())
    monkeypatch.setattr("app.agent.nodes.invoke_via_gateway", _fake_invoke_via_gateway)
    monkeypatch.setattr(
        "app.agent.nodes._build_diagnosis_prompt", lambda state, persona: "prompt de teste"
    )

    _run_diagnosis_agent({"description": "iFlow falhando", "retrieved_context": []}, "persona")

    assert len(_FakeReactAgent.captured_configs) == 1
    config = _FakeReactAgent.captured_configs[0]
    assert config["recursion_limit"] == 8


def test_run_diagnosis_agent_does_not_pass_web_tool_when_web_search_disabled(monkeypatch):
    """DA-29: com WEB_SEARCH_ENABLED=false o agente ReAct nao deve receber
    o tool de busca web — antes tools=[web_tool] era passado sempre."""
    import app.agent.nodes as nodes_module
    from app.config import Settings

    captured_tools: list = []

    class _CapturingReactAgent:
        def invoke(self, messages, config=None):
            return {
                "messages": [
                    _FakeMessage(
                        '{"matched_source": null, "probable_root_cause": "x", '
                        '"confidence": 0.5, "next_steps": []}'
                    )
                ]
            }

    def _capturing_create_react_agent(llm, tools=None, **kwargs):
        captured_tools.extend(tools or [])
        return _CapturingReactAgent()

    monkeypatch.setattr(nodes_module, "create_react_agent", _capturing_create_react_agent)
    monkeypatch.setattr(nodes_module, "invoke_via_gateway", _fake_invoke_via_gateway)
    monkeypatch.setattr(nodes_module, "_build_diagnosis_prompt", lambda state, persona: "p")
    monkeypatch.setattr(nodes_module, "settings", Settings(web_search_enabled=False))

    _run_diagnosis_agent({"description": "x", "retrieved_context": []}, "persona")

    assert captured_tools == [], (
        "Quando web_search_enabled=False, o ReAct agent nao deve receber nenhum tool"
    )


def _state_with_connector(is_mock: bool):
    from app.connectors.base import ConnectorResult

    return {
        "connector_data": ConnectorResult(
            source_system="ODATA",
            status="error",
            error_code=None,
            message="m",
            raw="",
            is_mock=is_mock,
        )
    }


def test_web_search_policy_gate(monkeypatch):
    """P0.2 + DA-57: WEB_SEARCH_POLICY aplicada no gate unico (node + tool ReAct)."""
    from app.agent import nodes

    monkeypatch.setattr(nodes.settings, "web_search_enabled", True)
    monkeypatch.setattr("app.llm.gateway.settings.sensitivity_default", "public")
    public_state = _state_with_connector(is_mock=True)
    confidential_state = _state_with_connector(is_mock=False)
    _approve(monkeypatch, "odata", "rfc")
    public_state["interface_type"] = "odata"
    confidential_state["interface_type"] = "rfc"

    monkeypatch.setattr(nodes.settings, "web_search_policy", "approved")
    assert nodes._web_search_allowed(confidential_state) is True

    monkeypatch.setattr(nodes.settings, "web_search_policy", "public_only")
    assert nodes._web_search_allowed(public_state) is True
    assert nodes._web_search_allowed(confidential_state) is False

    monkeypatch.setattr(nodes.settings, "web_search_policy", "disabled")
    assert nodes._web_search_allowed(public_state) is False

    monkeypatch.setattr(nodes.settings, "web_search_enabled", False)
    monkeypatch.setattr(nodes.settings, "web_search_policy", "approved")
    assert nodes._web_search_allowed(public_state) is False


def test_web_search_approved_policy_blocks_without_approved_source(monkeypatch):
    """DA-57: `approved` passou a significar o que o nome diz.

    Antes deste gate, o ramo `approved` retornava `True` incondicionalmente
    — nao havia lista de sites aprovados em lugar nenhum do codigo. Agora,
    sem linha habilitada em `web_search_sources` para o interface_type, a
    busca web nao acontece, mesmo com policy=approved e incidente publico.
    """
    from app.agent import nodes

    monkeypatch.setattr(nodes.settings, "web_search_enabled", True)
    monkeypatch.setattr(nodes.settings, "web_search_policy", "approved")
    monkeypatch.setattr("app.llm.gateway.settings.sensitivity_default", "public")
    _no_sources(monkeypatch)

    assert nodes._web_search_allowed(_state_with_connector(is_mock=True)) is False


def test_web_search_public_only_blocks_free_text_by_default(monkeypatch):
    """B-04: com sensitivity_default=confidential (default), public_only
    bloqueia a busca web para incidente so com texto do usuario."""
    from app.agent import nodes

    monkeypatch.setattr(nodes.settings, "web_search_enabled", True)
    monkeypatch.setattr(nodes.settings, "web_search_policy", "public_only")
    assert nodes._web_search_allowed({"description": "erro na interface"}) is False
