"""Testes do LLM Gateway (app/llm/factory.py) - sem dependencias
externas: cobrem a logica de selecao/validacao de provedor, nao a
chamada real ao modelo (isso e coberto pelos testes de integracao do
grafo, que ja exercitam o Ollama de verdade quando a stack local esta
no ar).

Os testes de `invoke_with_hybrid_fallback` (DA-20) tambem nao chamam
nenhum provider de verdade - usam um `build_and_invoke` fake que
levanta as excecoes exatas que os providers reais levantam em falha de
transporte (ver TRANSPORT_FAILURE_EXCEPTIONS), para provar a logica de
decisao (quando tenta fallback, quando NAO tenta, quando desiste) sem
depender de rede.
"""

import pytest

from app.config import Settings
from app.exceptions import ConfigurationError
from app.llm.factory import get_chat_model, invoke_with_hybrid_fallback


def test_default_provider_is_ollama():
    cfg = Settings(llm_provider="ollama")
    llm = get_chat_model(config=cfg)
    assert llm.__class__.__name__ == "ChatOllama"


def test_openai_without_api_key_raises_configuration_error():
    cfg = Settings(llm_provider="openai", openai_api_key="")
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        get_chat_model(config=cfg)


def test_openai_with_api_key_builds_chat_openai():
    cfg = Settings(llm_provider="openai", openai_api_key="sk-fake-for-test")
    llm = get_chat_model(config=cfg)
    assert llm.__class__.__name__ == "ChatOpenAI"


def test_azure_openai_missing_config_lists_missing_fields():
    cfg = Settings(llm_provider="azure_openai")
    with pytest.raises(ConfigurationError) as exc_info:
        get_chat_model(config=cfg)
    assert "AZURE_OPENAI_ENDPOINT" in str(exc_info.value)
    assert "AZURE_OPENAI_API_KEY" in str(exc_info.value)
    assert "AZURE_OPENAI_DEPLOYMENT" in str(exc_info.value)


def test_azure_openai_fully_configured_builds_client():
    cfg = Settings(
        llm_provider="azure_openai",
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_api_key="fake-key",
        azure_openai_deployment="gpt-4o-mini",
    )
    llm = get_chat_model(config=cfg)
    assert llm.__class__.__name__ == "AzureChatOpenAI"


def test_model_name_override_is_respected():
    cfg = Settings(llm_provider="ollama", llm_model="qwen2.5-coder:32b")
    llm = get_chat_model(model_name="qwen3:30b-a3b", config=cfg)
    assert llm.model == "qwen3:30b-a3b"


def test_hybrid_fallback_uses_primary_result_when_it_succeeds():
    cfg = Settings(llm_provider="ollama", llm_fallback_provider="openai", openai_api_key="sk-fake")
    calls = []

    def build_and_invoke(llm):
        calls.append(llm.__class__.__name__)
        return "resultado-primario"

    result, provider_used = invoke_with_hybrid_fallback(build_and_invoke, config=cfg)

    assert result == "resultado-primario"
    assert provider_used == "ollama"
    assert calls == ["ChatOllama"]  # fallback nunca foi construido


def test_hybrid_fallback_reraises_transport_error_when_no_fallback_configured():
    cfg = Settings(llm_provider="ollama", llm_fallback_provider="")

    def build_and_invoke(llm):
        raise ConnectionError("Ollama fora do ar (simulado)")

    with pytest.raises(ConnectionError, match="Ollama fora do ar"):
        invoke_with_hybrid_fallback(build_and_invoke, config=cfg)


def test_hybrid_fallback_switches_provider_on_transport_failure():
    cfg = Settings(llm_provider="ollama", llm_fallback_provider="openai", openai_api_key="sk-fake")
    calls = []

    def build_and_invoke(llm):
        calls.append(llm.__class__.__name__)
        if llm.__class__.__name__ == "ChatOllama":
            raise ConnectionError("Ollama fora do ar (simulado)")
        return "resultado-fallback"

    result, provider_used = invoke_with_hybrid_fallback(build_and_invoke, config=cfg)

    assert result == "resultado-fallback"
    assert provider_used == "openai"
    assert calls == ["ChatOllama", "ChatOpenAI"]


def test_hybrid_fallback_raises_configuration_error_when_both_providers_fail():
    cfg = Settings(llm_provider="ollama", llm_fallback_provider="openai", openai_api_key="sk-fake")

    def build_and_invoke(llm):
        raise ConnectionError(f"{llm.__class__.__name__} fora do ar (simulado)")

    with pytest.raises(ConfigurationError) as exc_info:
        invoke_with_hybrid_fallback(build_and_invoke, config=cfg)
    assert "ollama" in str(exc_info.value)
    assert "openai" in str(exc_info.value)


def test_hybrid_fallback_does_not_mask_application_errors():
    """DA-20: so falha de TRANSPORTE aciona o fallback. Um erro de
    aplicacao (prompt invalido, parsing, etc.) deve subir normalmente,
    mesmo com fallback configurado - senao um bug real vira
    silenciosamente uma segunda chamada de LLM (custo/latencia
    desnecessarios) escondendo o problema de verdade."""
    cfg = Settings(llm_provider="ollama", llm_fallback_provider="openai", openai_api_key="sk-fake")
    calls = []

    def build_and_invoke(llm):
        calls.append(llm.__class__.__name__)
        raise ValueError("prompt invalido (erro de aplicacao, nao de transporte)")

    with pytest.raises(ValueError, match="prompt invalido"):
        invoke_with_hybrid_fallback(build_and_invoke, config=cfg)
    assert calls == ["ChatOllama"]  # fallback nunca foi tentado
