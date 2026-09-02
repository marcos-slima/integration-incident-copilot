"""Testes do LLM Gateway (app/llm/factory.py) - sem dependencias
externas: cobrem a logica de selecao/validacao de provedor, nao a
chamada real ao modelo (isso e coberto pelos testes de integracao do
grafo, que ja exercitam o Ollama de verdade quando a stack local esta
no ar).
"""

import pytest

from app.config import Settings
from app.exceptions import ConfigurationError
from app.llm.factory import get_chat_model


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
