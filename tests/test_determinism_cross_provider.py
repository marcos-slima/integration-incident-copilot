"""P1.5 (auditoria eb90ede): Teste de determinismo cross-provider.

Verifica que `get_chat_model` passa `temperature=0.0` e `seed=42` para
OpenAI, AzureChatOpenAI e ChatOllama — os tres parametros que controlam
determinismo do output entre providers.

Por que isso importa: `seed` garante que, dado o mesmo prompt, a
resposta e reprodutivel (dentro do mesmo provider e versao de modelo).
Sem isso, um mesmo incidente pode gerar diagnosticos diferentes em
re-execucoes, dificultando benchmarks de regressao e comparacao
cross-provider. A garantia aqui e que PASSAMOS os parametros corretos
— o comportamento real de determinismo depende do provider (OpenAI
honra `seed` em chamadas live, Ollama tem suporte variavel por modelo,
AzureOpenAI herda o comportamento do OpenAI) e nao pode ser validado
em testes unitarios sem chamadas reais de rede.

Ver DA-02 (learnings.md) e factory.py para a decisao arquitetural de
seed=42/temperature=0.0 para todos os providers.
"""

import pytest

from app.config import Settings
from app.llm.factory import get_chat_model


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


def test_openai_temperature_is_zero():
    """ChatOpenAI deve ser inicializado com temperature=0.0."""
    cfg = Settings(llm_provider="openai", openai_api_key="sk-fake-determinism-test")
    llm = get_chat_model(config=cfg)
    assert llm.temperature == 0.0, (
        f"OpenAI temperature esperado 0.0, obtido {llm.temperature!r}. "
        "factory.py deve passar temperature=0.0 para garantir determinismo."
    )


def test_openai_seed_is_42():
    """ChatOpenAI deve ser inicializado com seed=42."""
    cfg = Settings(llm_provider="openai", openai_api_key="sk-fake-determinism-test")
    llm = get_chat_model(config=cfg)
    assert llm.seed == 42, (
        f"OpenAI seed esperado 42, obtido {llm.seed!r}. "
        "factory.py deve passar seed=42 para reproducibilidade cross-provider."
    )


# ---------------------------------------------------------------------------
# AzureOpenAI
# ---------------------------------------------------------------------------

_AZURE_CFG = dict(
    llm_provider="azure_openai",
    azure_openai_endpoint="https://example.openai.azure.com",
    azure_openai_api_key="fake-azure-key-determinism-test",
    azure_openai_deployment="gpt-4o-mini",
)


def test_azure_openai_temperature_is_zero():
    """AzureChatOpenAI deve ser inicializado com temperature=0.0."""
    cfg = Settings(**_AZURE_CFG)
    llm = get_chat_model(config=cfg)
    assert llm.temperature == 0.0, (
        f"AzureOpenAI temperature esperado 0.0, obtido {llm.temperature!r}."
    )


def test_azure_openai_seed_is_42():
    """AzureChatOpenAI deve ser inicializado com seed=42.

    Nota: `seed` e suportado pela API OpenAI e herdado pelo Azure OpenAI
    Service (mesma API de inferencia). O campo no cliente LangChain e
    `model_kwargs` para Azure — este teste verifica o atributo publico
    exposto apos construcao.
    """
    cfg = Settings(**_AZURE_CFG)
    llm = get_chat_model(config=cfg)
    # AzureChatOpenAI pode expor seed via model_kwargs ou atributo direto
    seed_value = getattr(llm, "seed", None) or llm.model_kwargs.get("seed")
    assert seed_value == 42, (
        f"AzureOpenAI seed esperado 42, obtido seed={getattr(llm, 'seed', None)!r} "
        f"model_kwargs.seed={llm.model_kwargs.get('seed')!r}."
    )


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


def test_ollama_temperature_is_zero():
    """ChatOllama deve ser inicializado com temperature=0.0."""
    cfg = Settings(llm_provider="ollama")
    llm = get_chat_model(config=cfg)
    assert llm.temperature == 0.0, (
        f"Ollama temperature esperado 0.0, obtido {llm.temperature!r}."
    )


def test_ollama_seed_is_42():
    """ChatOllama deve ser inicializado com seed=42.

    Nota: Ollama expoe `seed` como parte de `model_kwargs` (passado como
    `options` na chamada da API). Suporte real depende do modelo — mas
    o parametro deve ser passado para que modelos compatíveis (ex:
    llama3, mistral) possam usar.
    """
    cfg = Settings(llm_provider="ollama")
    llm = get_chat_model(config=cfg)
    # ChatOllama expoe seed diretamente ou via client_kwargs/model_kwargs
    seed_value = (
        getattr(llm, "seed", None)
        or getattr(llm, "model_kwargs", {}).get("seed")
        or getattr(llm, "client_kwargs", {}).get("seed")
    )
    assert seed_value == 42, (
        f"Ollama seed esperado 42, obtido seed={getattr(llm, 'seed', None)!r} "
        f"model_kwargs={getattr(llm, 'model_kwargs', {})!r} "
        f"client_kwargs={getattr(llm, 'client_kwargs', {})!r}."
    )


# ---------------------------------------------------------------------------
# Consistencia cross-provider: os tres providers usam os mesmos valores
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cfg_kwargs",
    [
        {"llm_provider": "ollama"},
        {"llm_provider": "openai", "openai_api_key": "sk-fake-determinism-test"},
        {
            "llm_provider": "azure_openai",
            "azure_openai_endpoint": "https://example.openai.azure.com",
            "azure_openai_api_key": "fake-azure-key-determinism-test",
            "azure_openai_deployment": "gpt-4o-mini",
        },
    ],
    ids=["ollama", "openai", "azure_openai"],
)
def test_temperature_is_consistent_across_providers(cfg_kwargs):
    """Todos os providers devem usar temperature=0.0 (B2 da auditoria)."""
    cfg = Settings(**cfg_kwargs)
    llm = get_chat_model(config=cfg)
    assert llm.temperature == 0.0, (
        f"Provider {cfg_kwargs['llm_provider']!r}: temperature esperado 0.0, "
        f"obtido {llm.temperature!r}."
    )
