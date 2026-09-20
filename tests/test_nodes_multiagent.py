"""DA-22 (Multi-agent): sap_diagnosis_node/saas_diagnosis_node -
verifica que cada sub-agente usa a persona correta e que o roteamento
condicional do grafo (app/agent/graph.py::_route_to_specialist) manda
o incidente para o node certo, sem depender de LLM real (mesmo padrao
de tests/test_llm_factory.py: mocka `invoke_via_gateway` (DA-26 - AI
Gateway, antes `invoke_with_hybrid_fallback`) diretamente, entao o
closure `_build_and_invoke` nunca roda de verdade - nao precisa de
Ollama nem de create_react_agent)."""

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


def _fake_gateway_factory(provider="ollama"):
    def _fake(build_and_invoke, state=None, prompt_text="", model_name=None, config=None):
        return {"messages": [_FakeMessage(_VALID_DIAGNOSIS_JSON)]}, provider

    return _fake


def test_sap_diagnosis_node_uses_sap_persona(monkeypatch):
    captured = {}

    def fake_prompt(state, persona):
        captured["persona"] = persona
        return "prompt qualquer"

    monkeypatch.setattr("app.agent.nodes._build_diagnosis_prompt", fake_prompt)
    monkeypatch.setattr("app.agent.nodes.invoke_via_gateway", _fake_gateway_factory())

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
    monkeypatch.setattr("app.agent.nodes.invoke_via_gateway", _fake_gateway_factory())

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


class _StubGraphBare:
    """Como _StubGraph, mas sem chaves extras - so o minimo que
    run_diagnosis le do final_state."""

    def invoke(self, initial_state):
        return {"diagnosis": {}, "report_markdown": ""}


def test_run_diagnosis_returns_incident_id_when_graph_rag_enabled_and_interface_known(
    monkeypatch,
):
    """DA-28: incident_id so faz sentido devolver quando o incidente
    de fato foi (ou sera) gravado no grafo - GraphRAG ligado E
    interface_type/identifier presentes (mesma condicao de
    upsert_incident_graph ser no-op ou nao)."""
    from app.agent import graph as graph_module
    from app.config import Settings
    from app.models import IncidentRequest

    monkeypatch.setattr(graph_module, "get_graph", lambda: _StubGraphBare())
    monkeypatch.setattr(graph_module, "settings", Settings(graph_rag_enabled=True))

    result = graph_module.run_diagnosis(
        IncidentRequest(
            description="IDoc travado", interface_type="rfc", identifier="RFC-IDOC-51-DEMO"
        )
    )

    assert result.incident_id is not None
    assert len(result.incident_id) > 0


def test_run_diagnosis_returns_none_incident_id_when_graph_rag_disabled(monkeypatch):
    from app.agent import graph as graph_module
    from app.config import Settings
    from app.models import IncidentRequest

    monkeypatch.setattr(graph_module, "get_graph", lambda: _StubGraphBare())
    monkeypatch.setattr(graph_module, "settings", Settings(graph_rag_enabled=False))

    result = graph_module.run_diagnosis(
        IncidentRequest(
            description="IDoc travado", interface_type="rfc", identifier="RFC-IDOC-51-DEMO"
        )
    )

    assert result.incident_id is None


def test_run_diagnosis_returns_none_incident_id_when_interface_missing(monkeypatch):
    """Mesmo com GraphRAG ligado, sem interface_type/identifier o
    incidente nunca e gravado (upsert_incident_graph e no-op) -
    devolver um id nesse caso enganaria o caller (sugeriria que ha algo
    para verificar depois, quando nao ha)."""
    from app.agent import graph as graph_module
    from app.config import Settings
    from app.models import IncidentRequest

    monkeypatch.setattr(graph_module, "get_graph", lambda: _StubGraphBare())
    monkeypatch.setattr(graph_module, "settings", Settings(graph_rag_enabled=True))

    result = graph_module.run_diagnosis(IncidentRequest(description="algo estranho"))

    assert result.incident_id is None


def test_run_diagnosis_threads_incident_id_into_initial_state(monkeypatch):
    """O id devolvido em DiagnosisResponse.incident_id tem que ser o
    MESMO passado no initial_state (e, por consequencia, usado por
    graph_write_node) - senao verify_incident() nunca acharia o
    incidente gravado."""
    from app.agent import graph as graph_module
    from app.config import Settings
    from app.models import IncidentRequest

    captured = {}

    class _CapturingGraph:
        def invoke(self, initial_state):
            captured.update(initial_state)
            return {"diagnosis": {}, "report_markdown": ""}

    monkeypatch.setattr(graph_module, "get_graph", lambda: _CapturingGraph())
    monkeypatch.setattr(graph_module, "settings", Settings(graph_rag_enabled=True))

    result = graph_module.run_diagnosis(
        IncidentRequest(
            description="IDoc travado", interface_type="rfc", identifier="RFC-IDOC-51-DEMO"
        )
    )

    assert captured["incident_id"] == result.incident_id


class _FakeLangfuseClient:
    def __init__(self, trace_id):
        self._trace_id = trace_id

    def get_current_trace_id(self):
        return self._trace_id


def test_run_diagnosis_populates_trace_id_from_langfuse_current_trace(monkeypatch):
    """Avaliacao externa (medio prazo, item 5): DiagnosisResponse.trace_id
    e capturado via get_client().get_current_trace_id() ENQUANTO ainda
    dentro do span "sap_copilot_diagnosis" (run_diagnosis e @observe) -
    independente de GraphRAG, diferente de incident_id."""
    from app.agent import graph as graph_module
    from app.config import Settings
    from app.models import IncidentRequest

    monkeypatch.setattr(graph_module, "get_graph", lambda: _StubGraphBare())
    monkeypatch.setattr(graph_module, "settings", Settings(graph_rag_enabled=False))
    monkeypatch.setattr(graph_module, "get_client", lambda: _FakeLangfuseClient("trace-xyz"))

    result = graph_module.run_diagnosis(IncidentRequest(description="IDoc travado"))

    assert result.trace_id == "trace-xyz"


def test_run_diagnosis_trace_id_is_none_when_langfuse_has_no_active_trace(monkeypatch):
    from app.agent import graph as graph_module
    from app.config import Settings
    from app.models import IncidentRequest

    monkeypatch.setattr(graph_module, "get_graph", lambda: _StubGraphBare())
    monkeypatch.setattr(graph_module, "settings", Settings(graph_rag_enabled=False))
    monkeypatch.setattr(graph_module, "get_client", lambda: _FakeLangfuseClient(None))

    result = graph_module.run_diagnosis(IncidentRequest(description="IDoc travado"))

    assert result.trace_id is None
