"""Factory de chat model - o "LLM Gateway" do Copilot.

Por que isso existe (contexto de negocio, nao so tecnico):

O projeto nasceu 100% Ollama/local (ver ADR de "local-first" original:
nenhuma dependencia de custo de API para estudar/prototipar). O
posicionamento atual do produto, porem, e viabilizar IA para empresas
que NAO conseguem adotar o SAP AI Core - seja por limitacao tecnica
(ainda em ECC on-premise, sem BTP/HANA Cloud) ou financeira (o AI Core
exige HANA Cloud como camada obrigatoria, o que sozinho custa na faixa
de dezenas de milhares de euros por ano, independente do quanto de IA
for consumido).

Isso nao significa que TODO cliente devera rodar local: alguns ja tem
uma assinatura OpenAI/Azure OpenAI contratada, ou querem mais
capacidade do que o hardware local aguenta para um caso especifico.
O grafo (app/agent/graph.py) so deveria se importar com a INTERFACE
(`with_structured_output`, `invoke`, callbacks) que todo
`BaseChatModel` do LangChain ja oferece - nao com qual provedor esta
por tras. Por isso este factory nao inventa uma interface propria (tipo
um `LLMProvider.generate()` do zero): ele so decide, a partir de
`Settings`, qual `BaseChatModel` do LangChain instanciar. Reaproveitar
o polimorfismo que a lib ja tem e menos codigo e menos superficie de
bug do que reimplementar o mesmo contrato.

Uso:
    from app.llm.factory import get_chat_model
    llm = get_chat_model()                       # usa settings.llm_provider
    llm = get_chat_model(model_name="qwen3:30b")  # override so do nome do modelo
"""

from app.config import Settings, settings
from app.exceptions import ConfigurationError


def get_chat_model(model_name: str | None = None, config: Settings | None = None):
    """Retorna uma instancia de `BaseChatModel` (LangChain) configurada
    conforme `config.llm_provider` (default: `settings` global).

    `model_name` sobrescreve so o nome do modelo (mesmo uso que
    `run_diagnosis(..., llm_model=...)` ja fazia antes desta mudanca,
    ex: promptfoo_provider.py comparando modelos) - o provedor em si
    continua vindo de `config.llm_provider`.
    """
    cfg = config or settings
    provider = cfg.llm_provider

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model_name or cfg.llm_model,
            base_url=cfg.ollama_host,
            temperature=0.0,
            seed=42,
        )

    if provider == "openai":
        if not cfg.openai_api_key:
            raise ConfigurationError(
                "llm_provider='openai' exige OPENAI_API_KEY configurada no .env "
                "(ou OPENAI_BASE_URL, se for um endpoint compativel self-hosted "
                "tipo vLLM/LM Studio que nao exige key real - nesse caso passe "
                "qualquer valor nao-vazio)."
            )
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise ConfigurationError(
                "llm_provider='openai' exige o pacote opcional 'langchain-openai' "
                "- instale com: uv sync --extra openai"
            ) from exc

        return ChatOpenAI(
            model=model_name or cfg.llm_model,
            api_key=cfg.openai_api_key,
            base_url=cfg.openai_base_url or None,
            temperature=0.0,
            seed=42,
        )

    if provider == "azure_openai":
        missing = [
            name
            for name, value in (
                ("AZURE_OPENAI_ENDPOINT", cfg.azure_openai_endpoint),
                ("AZURE_OPENAI_API_KEY", cfg.azure_openai_api_key),
                ("AZURE_OPENAI_DEPLOYMENT", cfg.azure_openai_deployment),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(
                "llm_provider='azure_openai' exige as seguintes variaveis no "
                f".env, ainda nao configuradas: {', '.join(missing)}"
            )
        try:
            from langchain_openai import AzureChatOpenAI
        except ImportError as exc:
            raise ConfigurationError(
                "llm_provider='azure_openai' exige o pacote opcional "
                "'langchain-openai' - instale com: uv sync --extra openai"
            ) from exc

        return AzureChatOpenAI(
            azure_endpoint=cfg.azure_openai_endpoint,
            api_key=cfg.azure_openai_api_key,
            azure_deployment=cfg.azure_openai_deployment,
            api_version=cfg.azure_openai_api_version,
            temperature=0.0,
            seed=42,
        )

    raise ConfigurationError(f"llm_provider desconhecido: {provider!r}")
