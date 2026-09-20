"""DA-22 (Multi-agent): sap_diagnosis_node/saas_diagnosis_node -
verifica que cada sub-agente usa a persona correta e que o roteamento
condicional do grafo (app/agent/graph.py::_route_to_specialist) manda
o incidente para o node certo, sem depender de LLM real (mesmo padrao
de tests/test_llm_factory.py: mocka `invoke_with_hybrid_fallback`
diretamente, entao o closure `_build_and_invoke` nunca roda de verdade
- nao precisa de Ollama nem de create_react_agent)."""

from app.agent.graph import _route_to_specialist
from app.agent.nodes import (
    _ENTERPRISE_SPECIALIST_PERSONA,
    _SAP_SPECIALIST_PERSONA,
    saas_diagnosis_node,
    sap_diagnosis_node,
)


class _FakeMessage:
    def __init__(self, content):
        self.content = content


_VALID_DIAGNOSIS_JSON = (
    '{"matched_source": null, "probable_root_cause": "causa X", '
    '"confidence": 0.5, "next_steps": ["passo 1"]}'
)


def _fake_hybrid_fallback_factory(provider="ollama"):
    def _fake(build_and_invoke, model_name=None, config=None):
        return {"messages": [_FakeMessage(_VALID_DIAGNOSIS_JSON)]}, provider

    return _fake


def test_sap_diagnosis_node_uses_sap_persona(monkeypatch):
    captured = {}

    def fake_prompt(state, persona):
        captured["persona"] = persona
        return "prompt qualquer"

    monkeypatch.setattr("app.agent.nodes._build_diagnosis_prompt", fake_prompt)
    monkeypatch.setattr(
        "app.agent.nodes.invoke_with_hybrid_fallback", _fake_hybrid_fallback_factory()
    )

    result = sap_diagnosis_node({"description": "IDoc travado", "retrieved_context": []})

    assert captured["persona"] == _SAP_SPECIALIST_PERSONA
    assert result["diagnosis"]["probable_root_cause"] == "causa X"
    assert result["diagnosis"]["llm_provider_used"] == "ollama"


def test_saas_diagnosis_node_uses_enterprise_persona(monkeypatch):
    captured = {}

    def fake_prompt(state, persona):
        captured["persona"] = persona
        return "prompt qualquer"

    monkeypatch.setattr("app.agent.nodes._build_diagnosis_prompt", fake_prompt)
    monkeypatch.setattr(
        "app.agent.nodes.invoke_with_hybrid_fallback", _fake_hybrid_fallback_factory()
    )

    result = saas_diagnosis_node(
        {"description": "webhook falhando no Salesforce", "retrieved_context": []}
    )

    assert captured["persona"] == _ENTERPRISE_SPECIALIST_PERSONA
    assert result["diagnosis"]["probable_root_cause"] == "causa X"


def test_route_to_specialist_sends_sap_domain_to_sap_node():
    assert _route_to_specialist({"agent_domain": "sap"}) == "sap_diagnose"


def test_route_to_specialist_sends_saas_and_generic_to_enterprise_node():
    assert _route_to_specialist({"agent_domain": "saas"}) == "saas_diagnose"
    assert _route_to_specialist({"agent_domain": "generic"}) == "saas_diagnose"


def test_route_to_specialist_defaults_to_enterprise_node_when_domain_missing():
    # Salvaguarda: se por algum motivo o supervisor nao rodou e
    # agent_domain nao esta no state, o roteamento nao deve quebrar -
    # cai no especialista generalista em vez de estourar KeyError.
    assert _route_to_specialist({}) == "saas_diagnose"


def test_run_diagnosis_exposes_agent_domain_in_response(monkeypatch):
    """DA-22: agent_domain decidido pelo supervisor deve chegar ate
    DiagnosisResponse, mesmo padrao de transparencia usado para
    llm_provider_used (DA-20) - sem isso o roteamento multi-agente
    fica invisivel para quem consome a API."""
    from app.agent import graph as graph_module
    from app.models import IncidentRequest

    class _StubGraph:
        def invoke(self, initial_state):
            return {
                "diagnosis": {
                    "probable_root_cause": "causa Y",
                    "confidence": 0.6,
                    "matched_source": None,
                    "next_steps": [],
                    "llm_provider_used": "ollama",
                },
                "report_markdown": "## relatorio",
                "agent_domain": "saas",
            }

    monkeypatch.setattr(graph_module, "get_graph", lambda: _StubGraph())

    result = graph_module.run_diagnosis(
        IncidentRequest(description="webhook falhando", interface_type="salesforce")
    )

    assert result.agent_domain == "saas"
    assert result.probable_root_cause == "causa Y"
