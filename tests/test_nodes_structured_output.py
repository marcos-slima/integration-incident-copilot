"""Avaliacao externa (curto prazo, item 5): "structured output de
verdade" - response_format=DiagnosisModel no create_react_agent
(app/agent/nodes.py::_run_diagnosis_agent). Mocka create_react_agent e
invoke_via_gateway diretamente (nunca um LLM real), pra provar as 3
camadas de resiliencia:

  1. structured_response populado -> usado direto (caminho novo/principal)
  2. a chamada de structured output falha -> refaz SEM response_format,
     cai no parsing por regex existente (fallback)
  3. falha de TRANSPORTE (Ollama fora do ar) -> propaga sem tratamento
     especial, pro AI Gateway (invoke_via_gateway, DA-26) decidir
     circuit breaker/fallback normalmente - nao mascarada como se fosse
     um problema de structured output.
"""

import app.agent.nodes as nodes_module
from app.agent.nodes import sap_diagnosis_node
from app.agent.state import DiagnosisModel


class _FakeMessage:
    def __init__(self, content):
        self.content = content


def _fake_gateway_calling_builder(provider="ollama"):
    """Diferente do _fake_gateway_factory usado em
    test_nodes_multiagent.py (que ignora build_and_invoke e devolve um
    resultado fixo) - este DE FATO chama build_and_invoke(fake_llm),
    pra exercitar o try/except dentro de _build_and_invoke."""

    def _fake(build_and_invoke, state=None, prompt_text="", model_name=None, config=None):
        return build_and_invoke(llm="fake-llm-placeholder"), provider

    return _fake


def _stub_prompt(state, persona):
    return "prompt qualquer"


def test_structured_response_used_directly_when_present(monkeypatch):
    diagnosis_instance = DiagnosisModel(
        matched_source="idoc_status_51.md",
        probable_root_cause="Pool de dialogo esgotado",
        confidence=0.85,
        next_steps=["Verificar SM50"],
    )

    class _FakeReactAgent:
        def invoke(self, messages, config=None):
            return {
                "messages": [_FakeMessage("texto qualquer, nao deveria ser parseado")],
                "structured_response": diagnosis_instance,
            }

    def _fake_create_react_agent(llm, tools, response_format=None):
        assert response_format is DiagnosisModel  # caminho principal sempre pede structured output
        return _FakeReactAgent()

    monkeypatch.setattr(nodes_module, "_build_diagnosis_prompt", _stub_prompt)
    monkeypatch.setattr(nodes_module, "create_react_agent", _fake_create_react_agent)
    monkeypatch.setattr(nodes_module, "invoke_via_gateway", _fake_gateway_calling_builder())

    result = sap_diagnosis_node({"description": "IDoc travado", "retrieved_context": []})

    assert result["diagnosis"]["probable_root_cause"] == "Pool de dialogo esgotado"
    # os guardrails deterministicos (_apply_confidence_guardrails) rodam
    # sobre o resultado independente da origem (structured_response ou
    # regex) - sem contexto/conector real, o teto de evidencia (0.25)
    # sempre vence a confianca autoavaliada pelo LLM (0.85 aqui).
    assert result["diagnosis"]["model_confidence"] == 0.25
    assert result["diagnosis"]["matched_source"] == "idoc_status_51.md"


def test_falls_back_to_regex_when_structured_output_call_raises(monkeypatch):
    """A camada 2: a chamada COM response_format falha (ex: modelo nao
    suporta with_structured_output direito) - refaz sem response_format
    e cai no parsing por regex existente, sem propagar a excecao."""
    valid_json = (
        '{"matched_source": null, "probable_root_cause": "causa via regex", '
        '"confidence": 0.4, "next_steps": ["passo 1"]}'
    )

    call_count = {"n": 0}

    class _FakeReactAgentStructured:
        def invoke(self, messages, config=None):
            raise ValueError("modelo nao suporta with_structured_output bem o suficiente")

    class _FakeReactAgentPlain:
        def invoke(self, messages, config=None):
            return {"messages": [_FakeMessage(valid_json)]}

    def _fake_create_react_agent(llm, tools, response_format=None):
        call_count["n"] += 1
        if response_format is not None:
            return _FakeReactAgentStructured()
        return _FakeReactAgentPlain()

    monkeypatch.setattr(nodes_module, "_build_diagnosis_prompt", _stub_prompt)
    monkeypatch.setattr(nodes_module, "create_react_agent", _fake_create_react_agent)
    monkeypatch.setattr(nodes_module, "invoke_via_gateway", _fake_gateway_calling_builder())

    result = sap_diagnosis_node({"description": "IDoc travado", "retrieved_context": []})

    assert call_count["n"] == 2  # 1a tentativa (structured) + 2a (fallback, sem response_format)
    assert result["diagnosis"]["probable_root_cause"] == "causa via regex"
    assert (
        result["diagnosis"]["model_confidence"] == 0.25
    )  # teto de evidencia, mesmo motivo do teste acima


def test_transport_failure_propagates_without_retry_inside_build_and_invoke(monkeypatch):
    """A camada 3: falha de TRANSPORTE nao deve ser tratada como falha
    de structured output - precisa subir intacta pro invoke_via_gateway
    decidir (circuit breaker/hybrid fallback, DA-26/DA-20), nao ser
    engolida e reescrita como uma segunda tentativa aqui dentro."""

    class _FakeReactAgentStructured:
        def invoke(self, messages, config=None):
            raise ConnectionError("Ollama fora do ar")

    call_count = {"n": 0}

    def _fake_create_react_agent(llm, tools, response_format=None):
        call_count["n"] += 1
        return _FakeReactAgentStructured()

    monkeypatch.setattr(nodes_module, "_build_diagnosis_prompt", _stub_prompt)
    monkeypatch.setattr(nodes_module, "create_react_agent", _fake_create_react_agent)
    monkeypatch.setattr(nodes_module, "invoke_via_gateway", _fake_gateway_calling_builder())

    try:
        sap_diagnosis_node({"description": "IDoc travado", "retrieved_context": []})
        raised = False
    except ConnectionError:
        raised = True

    assert raised
    assert call_count["n"] == 1  # nao tentou de novo sem response_format


def test_missing_structured_response_key_falls_back_to_regex(monkeypatch):
    """Chamada direta a create_react_agent sem response_format (ou um
    resultado que simplesmente nao populou structured_response) tem que
    continuar caindo no parsing por regex - comportamento identico ao
    de antes desta mudanca."""
    valid_json = (
        '{"matched_source": "doc.md", "probable_root_cause": "causa antiga", '
        '"confidence": 0.6, "next_steps": []}'
    )

    class _FakeReactAgent:
        def invoke(self, messages, config=None):
            return {"messages": [_FakeMessage(valid_json)]}  # sem "structured_response"

    monkeypatch.setattr(nodes_module, "_build_diagnosis_prompt", _stub_prompt)
    monkeypatch.setattr(
        nodes_module,
        "create_react_agent",
        lambda llm, tools, response_format=None: _FakeReactAgent(),
    )
    monkeypatch.setattr(nodes_module, "invoke_via_gateway", _fake_gateway_calling_builder())

    result = sap_diagnosis_node({"description": "IDoc travado", "retrieved_context": []})

    assert result["diagnosis"]["probable_root_cause"] == "causa antiga"
    assert result["diagnosis"]["matched_source"] == "doc.md"
