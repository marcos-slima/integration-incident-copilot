"""DA-26 (AI Gateway v1) - testes de app/llm/gateway.py: policy de
roteamento por sensibilidade de dado, circuit breaker e budget, sem
depender de nenhum provider real (o "build_and_invoke" fake levanta as
mesmas excecoes de transporte que os providers reais levantam em
falha de rede, exatamente como tests/test_llm_factory.py ja fazia para
invoke_with_hybrid_fallback - este modulo so acrescenta a camada de
policy/circuit/budget em cima)."""

import httpx
import pytest

from app.config import Settings
from app.connectors.base import ConnectorResult
from app.exceptions import ConfigurationError
from app.llm.gateway import (
    CircuitBreaker,
    PolicyViolationError,
    _estimate_cost_usd,
    _select_allowed_providers,
    circuit_breaker,
    classify_sensitivity,
    invoke_via_gateway,
)


@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    circuit_breaker.reset()
    yield
    circuit_breaker.reset()


def _real_connector_data(source_system="OData"):
    return ConnectorResult(
        source_system=source_system,
        status="error",
        error_code="401",
        message="Unauthorized",
        raw="{}",
        is_mock=False,
        is_fallback=False,
    )


def _mock_connector_data():
    return ConnectorResult(
        source_system="RFC",
        status="error",
        error_code=None,
        message="mock generico",
        raw="{}",
        is_mock=True,
        is_fallback=False,
    )


# ---------------------------------------------------------------------
# classify_sensitivity
# ---------------------------------------------------------------------


def test_real_connector_data_is_confidential():
    assert classify_sensitivity({"connector_data": _real_connector_data()}) == "confidential"


@pytest.fixture
def public_default(monkeypatch):
    """B-04: incidentes sem dado real de conector sao 'confidential' por
    default - testes de roteamento de dado publico ligam o opt-out."""
    monkeypatch.setattr("app.llm.gateway.settings.sensitivity_default", "public")


def test_mock_connector_data_uses_sensitivity_default():
    state = {"connector_data": _mock_connector_data()}
    assert classify_sensitivity(state) == "confidential"


def test_no_connector_data_is_confidential_by_default():
    """B-04: texto livre pode conter dado empresarial nao reconhecido pela
    redacao por regex - nao classificado = sensivel."""
    assert classify_sensitivity({"description": "erro generico"}) == "confidential"


def test_no_connector_data_is_public_when_opted_out(public_default):
    assert classify_sensitivity({"description": "erro generico"}) == "public"
    assert classify_sensitivity({"connector_data": _mock_connector_data()}) == "public"


# ---------------------------------------------------------------------
# DA-43/GOV-01: sensibilidade declarada pelo cliente (so eleva) e registry
# ---------------------------------------------------------------------


def test_client_public_e_respeitado_quando_padrao_tambem_e_public(public_default):
    assert classify_sensitivity({"sensitivity_level": "public"}) == "public"


def test_client_public_nao_rebaixa_padrao_confidential():
    """GOV-01 (validacao 2026-10-06): a declaracao do cliente so ELEVA."""
    assert classify_sensitivity({"sensitivity_level": "public"}) == "confidential"


@pytest.mark.parametrize("nivel", ["internal", "confidential", "secret"])
def test_client_nao_publico_eleva_mesmo_com_padrao_public(public_default, nivel):
    assert classify_sensitivity({"sensitivity_level": nivel}) == "confidential"


def test_pii_detected_eleva_mesmo_com_padrao_public(public_default):
    assert classify_sensitivity({"pii_detected": True}) == "confidential"


def test_connector_real_prevalece_sobre_client_public(public_default):
    state = {"sensitivity_level": "public", "connector_data": _real_connector_data()}
    assert classify_sensitivity(state) == "confidential"


def test_campos_de_sensibilidade_sobrevivem_ao_stategraph():
    """Regressao GOV-01: o StateGraph(CopilotState) descarta chaves fora do
    schema - a versao anterior gravava `incident_request`, que sumia antes
    do primeiro node."""
    from langgraph.graph import END, StateGraph

    from app.agent.state import CopilotState

    visto = {}

    def probe(state):
        visto.update(state)
        return {}

    g = StateGraph(CopilotState)
    g.add_node("probe", probe)
    g.set_entry_point("probe")
    g.add_edge("probe", END)
    g.compile().invoke({"description": "x", "sensitivity_level": "secret", "pii_detected": True})
    assert visto["sensitivity_level"] == "secret"
    assert visto["pii_detected"] is True
    assert classify_sensitivity(visto) == "confidential"


def test_run_diagnosis_propaga_sensibilidade_do_request(monkeypatch):
    import app.agent.graph as graph_mod
    from app.models import IncidentRequest

    capturado = {}

    def fake_invoke(initial_state):
        capturado.update(initial_state)
        raise RuntimeError("parar aqui")

    monkeypatch.setattr(graph_mod, "_invoke_graph_with_timeout", fake_invoke)
    req = IncidentRequest(description="erro", sensitivity_level="secret", pii_detected=True)
    with pytest.raises(RuntimeError, match="parar aqui"):
        graph_mod.run_diagnosis(req)
    assert capturado["sensitivity_level"] == "secret"
    assert capturado["pii_detected"] is True


def test_registry_com_destino_remoto_e_barrado_para_confidential(monkeypatch):
    """GOV-01: no modo gerenciado o base_url do registro (texto livre do
    admin) e reavaliado. Ollama do .env em loopback, registro apontando
    para host remoto: dado confidencial nao sai."""
    from app.llm import factory, gateway

    cfg = Settings(
        _env_file=None,
        llm_provider="ollama",
        llm_fallback_provider="",
        ollama_host="http://127.0.0.1:11434",
        llm_registry_db=True,
    )
    monkeypatch.setattr(
        "app.admin.runtime.resolve_runtime_model",
        lambda provider, model, c: {
            "model_id": "m",
            "base_url": "https://gpu.terceiro.example.com",
        },
    )
    chamado = []
    monkeypatch.setattr(gateway, "get_chat_model", lambda **kw: chamado.append(kw))
    monkeypatch.setattr(factory, "get_chat_model", lambda **kw: chamado.append(kw))
    with pytest.raises(PolicyViolationError, match="destino resolvido pelo registro"):
        gateway.invoke_via_gateway(
            lambda llm: None, state={"description": "x"}, prompt_text="x", config=cfg
        )
    assert chamado == []


# ---------------------------------------------------------------------
# _select_allowed_providers
# ---------------------------------------------------------------------


def test_public_data_allows_local_and_cloud():
    allowed = _select_allowed_providers("public", "ollama", "openai")
    assert allowed == ["ollama", "openai"]


def test_confidential_data_excludes_cloud_even_as_fallback():
    allowed = _select_allowed_providers("confidential", "ollama", "openai")
    assert allowed == ["ollama"]


def test_confidential_data_with_cloud_primary_and_no_local_yields_no_providers():
    allowed = _select_allowed_providers("confidential", "openai", "")
    assert allowed == []


def test_allowed_providers_deduplicated():
    allowed = _select_allowed_providers("public", "ollama", "")
    assert allowed == ["ollama"]


# ---------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------


def test_circuit_starts_closed():
    cb = CircuitBreaker()
    assert cb.is_open("ollama", cooldown_seconds=30.0) is False


def test_circuit_opens_after_threshold_failures():
    cb = CircuitBreaker()
    cb.record_failure("ollama", failure_threshold=3)
    cb.record_failure("ollama", failure_threshold=3)
    assert cb.is_open("ollama", cooldown_seconds=30.0) is False  # so 2 falhas ainda
    cb.record_failure("ollama", failure_threshold=3)
    assert cb.is_open("ollama", cooldown_seconds=30.0) is True  # 3a falha abre


def test_circuit_closes_after_success():
    cb = CircuitBreaker()
    for _ in range(3):
        cb.record_failure("ollama", failure_threshold=3)
    assert cb.is_open("ollama", cooldown_seconds=30.0) is True

    cb.record_success("ollama")
    assert cb.is_open("ollama", cooldown_seconds=30.0) is False


def test_circuit_reopens_after_cooldown_expires():
    cb = CircuitBreaker()
    for _ in range(3):
        cb.record_failure("ollama", failure_threshold=3)
    # cooldown=0 -> ja expirou no instante seguinte
    assert cb.is_open("ollama", cooldown_seconds=0.0) is False


def test_circuit_is_per_provider():
    cb = CircuitBreaker()
    for _ in range(3):
        cb.record_failure("ollama", failure_threshold=3)
    assert cb.is_open("ollama", cooldown_seconds=30.0) is True
    assert cb.is_open("openai", cooldown_seconds=30.0) is False


# ---------------------------------------------------------------------
# _estimate_cost_usd
# ---------------------------------------------------------------------


def test_ollama_cost_is_always_zero():
    assert _estimate_cost_usd("ollama", "x" * 100_000) == 0.0


def test_cloud_cost_scales_with_text_length():
    small = _estimate_cost_usd("openai", "x" * 400)
    large = _estimate_cost_usd("openai", "x" * 40_000)
    assert large > small > 0.0


# ---------------------------------------------------------------------
# invoke_via_gateway
# ---------------------------------------------------------------------


def test_confidential_data_never_reaches_cloud_even_when_local_configured_as_primary():
    cfg = Settings(llm_provider="ollama", llm_fallback_provider="openai", openai_api_key="fake")
    calls = []

    def fake_build_and_invoke(llm):
        calls.append(llm.__class__.__name__)
        return "diagnostico ok"

    _result, provider = invoke_via_gateway(
        fake_build_and_invoke,
        state={"connector_data": _real_connector_data()},
        prompt_text="incidente confidencial",
        config=cfg,
    )

    assert provider == "ollama"
    assert calls == ["ChatOllama"]  # nunca tentou ChatOpenAI


def test_confidential_data_with_only_cloud_configured_raises_policy_violation():
    cfg = Settings(llm_provider="openai", llm_fallback_provider="", openai_api_key="fake")

    def fake_build_and_invoke(llm):
        raise AssertionError("nao deveria ser chamado - policy bloqueia antes")

    with pytest.raises(PolicyViolationError, match="confidential"):
        invoke_via_gateway(
            fake_build_and_invoke,
            state={"connector_data": _real_connector_data()},
            prompt_text="incidente confidencial",
            config=cfg,
        )


def test_public_data_falls_back_to_cloud_on_transport_failure(public_default):
    cfg = Settings(llm_provider="ollama", llm_fallback_provider="openai", openai_api_key="fake")
    calls = []

    def fake_build_and_invoke(llm):
        calls.append(llm.__class__.__name__)
        if llm.__class__.__name__ == "ChatOllama":
            raise ConnectionError("ollama fora do ar")
        return "diagnostico via fallback"

    _result, provider = invoke_via_gateway(
        fake_build_and_invoke,
        state={"description": "pergunta generica"},
        prompt_text="pergunta generica",
        config=cfg,
    )

    assert provider == "openai"
    assert calls == ["ChatOllama", "ChatOpenAI"]


def test_all_providers_failing_raises_configuration_error():
    cfg = Settings(llm_provider="ollama", llm_fallback_provider="openai", openai_api_key="fake")

    def fake_build_and_invoke(llm):
        raise httpx.ConnectError("indisponivel")

    with pytest.raises(ConfigurationError):
        invoke_via_gateway(
            fake_build_and_invoke,
            state={"description": "x"},
            prompt_text="x",
            config=cfg,
        )


def test_budget_rejection_skips_call_without_invoking_provider(public_default):
    cfg = Settings(llm_provider="openai", openai_api_key="fake", llm_gateway_max_cost_usd=0.0001)

    def fake_build_and_invoke(llm):
        raise AssertionError("nao deveria ser chamado - orcamento estourado antes")

    with pytest.raises(PolicyViolationError, match="custo"):
        invoke_via_gateway(
            fake_build_and_invoke,
            state={"description": "x"},
            prompt_text="x" * 40_000,  # texto grande o suficiente pra estourar o teto minusculo
            config=cfg,
        )


def test_open_circuit_skips_provider_without_invoking_it(public_default):
    cfg = Settings(
        llm_provider="ollama",
        llm_fallback_provider="openai",
        openai_api_key="fake",
        llm_gateway_circuit_failure_threshold=1,
        llm_gateway_circuit_cooldown_seconds=999.0,
    )

    def fake_first(llm):
        if llm.__class__.__name__ == "ChatOllama":
            raise ConnectionError("ollama fora do ar")
        return "ok via fallback"

    # primeira chamada: ollama falha (threshold=1 -> circuito abre), cai pro fallback
    _result, provider = invoke_via_gateway(
        fake_first,
        state={"description": "x"},
        prompt_text="x",
        config=cfg,
    )
    assert provider == "openai"

    # segunda chamada: circuito do ollama deve estar aberto (cooldown longo) -
    # nao deve nem tentar chamar ChatOllama de novo
    calls = []

    def fake_second(llm):
        calls.append(llm.__class__.__name__)
        return "ok"

    _result2, provider2 = invoke_via_gateway(
        fake_second,
        state={"description": "x"},
        prompt_text="x",
        config=cfg,
    )
    assert provider2 == "openai"
    assert calls == ["ChatOpenAI"]  # ollama pulado direto, circuito aberto
