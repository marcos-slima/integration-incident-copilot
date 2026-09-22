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

    tool = _make_web_search_tool({"interface_type": "odata"})
    tool.invoke({"query": "BAPI_MATERIAL_SAVEDATA authorization error"})

    assert "BAPI_MATERIAL_SAVEDATA authorization error" in _FakeDDGS.captured_queries[0]


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
