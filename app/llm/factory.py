"""Factory de chat model - camada de ABSTRACAO de provider (LangChain
BaseChatModel), usada pelo AI Gateway (app/llm/gateway.py, DA-26).

Ate a DA-26, este modulo era chamado de "LLM Gateway" no comentario
abaixo - uma revisao arquitetural externa apontou corretamente que
isso era, tecnicamente, um LLM Provider Factory / Abstraction Layer,
nao um Gateway de verdade (sem policy, budget, circuit breaker
centralizado). O nome do modulo nao mudou (factory.py continua
descrevendo bem o que ele faz: decide QUAL BaseChatModel instanciar),
mas quem QUER um AI Gateway de verdade deve chamar
app.llm.gateway.invoke_via_gateway(), nao invoke_with_hybrid_fallback()
diretamente - ver app/agent/nodes.py::_run_diagnosis_agent.

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

Hybrid Inference (DA-20): quando `settings.llm_fallback_provider` esta
configurado, `invoke_with_hybrid_fallback()` (abaixo) roda uma chamada
com o provider primario e, SO em caso de falha de transporte (Ollama
fora do ar, timeout - nao erro de aplicacao), refaz a MESMA chamada com
o provider de fallback antes de desistir. Ver app/agent/nodes.py
(_run_diagnosis_agent, usado pelos sub-agentes sap_diagnosis_node/
saas_diagnosis_node - DA-22) para o uso real.
"""

import logging

import httpx

from app.config import Settings, settings
from app.exceptions import ConfigurationError

# DA-45: tabela de capacidades por ORIGIN real. Importar daqui (e nao de
# app.llm.gateway) e' obrigatorio: o gateway importa o factory, entao o
# factory importando o gateway viraria ciclo.
from app.llm.capabilities import should_send_seed
from app.llm.origins import resolve_provider_origin

logger = logging.getLogger(__name__)

# Excecoes que sinalizam "o provider esta inalcancavel agora" (rede,
# timeout) - NAO erros de aplicacao (prompt invalido, resposta
# malformada, credencial errada) que devem continuar subindo
# normalmente em vez de mascarados por uma tentativa de fallback.
# `ConnectionError` (builtin) e o que o pacote `ollama` levanta ao
# nao conseguir conectar (ver ollama._client._request_raw, que
# converte `httpx.ConnectError` nisso); providers OpenAI-compativeis
# (langchain-openai, sobre httpx) levantam `httpx.ConnectError`/
# `httpx.TimeoutException` diretamente.
TRANSPORT_FAILURE_EXCEPTIONS = (ConnectionError, httpx.ConnectError, httpx.TimeoutException)


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

    # DA-46: modo gerenciado (LLM_REGISTRY_DB=true) — resolve modelo,
    # base_url e credencial a partir do registro (app/admin/) em vez do
    # .env. FAIL-CLOSED: se o registro nao tiver um modelo habilitado
    # para a origem em uso (ou, p/ origem cloud, uma credencial
    # cifrada), a chamada e rejeitada — nunca cai de volta para o .env.
    if cfg.llm_registry_db:
        from app.admin.runtime import resolve_runtime_model

        resolved = resolve_runtime_model(provider, model_name or cfg.llm_model, cfg)
        if resolved is None:
            _origin = resolve_provider_origin(provider, cfg) or "(origem desconhecida)"
            raise ConfigurationError(
                f"llm_registry_db=true: nenhum modelo habilitado para a origem "
                f"'{_origin}' (provider '{provider}') — registre o modelo (e a "
                "credencial cifrada, para origem cloud) em /admin antes de usar "
                "o modo gerenciado."
            )
        cfg = _apply_registry_resolution(provider, cfg, resolved)

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model_name or cfg.llm_model,
            base_url=cfg.ollama_host,
            temperature=0.0,
            seed=42,
            # Avaliacao externa (curto prazo, item 4): sem isso, o
            # cliente HTTP interno do ollama (httpx) nao tem limite de
            # tempo - um Ollama travado (processo vivo, sem responder)
            # prenderia a chamada indefinidamente, ao contrario de um
            # Ollama fora do ar (isso ja cai em TRANSPORT_FAILURE_EXCEPTIONS
            # rapido, via ConnectionError).
            client_kwargs={"timeout": cfg.llm_request_timeout_seconds},
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

        # DA-2: seed=42 por padrao, mas so quando o DESTINO aceita o
        # campo. Este e' o unico branch que precisa consultar a tabela de
        # capacidades: e' o unico que alcanca um endpoint
        # OpenAI-compatible de terceiro, onde `seed` pode nao existir
        # (Gemini responde 400 e recusa a request INTEIRA). Ollama e Azure
        # tem contrato proprio e seguem com seed=42 fixo acima/abaixo.
        #
        # `llm_send_seed=None` significa "pergunte a tabela" - e' o estado
        # que mantem DA-2 como default. Tratar None como False (a
        # verdadeabilidade ingenua de antes) desligava a invariante de
        # determinismo em TODO provider, por causa de um so incompativel.
        _origin = resolve_provider_origin("openai", cfg)
        _send_seed = should_send_seed(_origin, cfg.llm_send_seed)

        return ChatOpenAI(
            model=model_name or cfg.llm_model,
            api_key=cfg.openai_api_key,
            base_url=cfg.openai_base_url or None,
            temperature=0.0,
            seed=42 if _send_seed else None,
            request_timeout=cfg.llm_request_timeout_seconds,
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
            request_timeout=cfg.llm_request_timeout_seconds,
        )

    raise ConfigurationError(f"llm_provider desconhecido: {provider!r}")


def _apply_registry_resolution(provider: str, cfg: Settings, resolved: dict) -> Settings:
    """Sobrescreve o Settings com o que o registro resolveu (DA-46).

    `resolved` vem de app/admin/runtime.py e deve conter model_id,
    base_url e (para origem cloud) api_key. Campos mapeados por provider
    para que os branches de get_chat_model continuem lendo os mesmos
    atributos de sempre."""
    if provider == "ollama":
        return cfg.model_copy(
            update={
                "llm_model": resolved["model_id"],
                "ollama_host": resolved.get("base_url") or cfg.ollama_host,
            }
        )
    if provider == "openai":
        return cfg.model_copy(
            update={
                "llm_model": resolved["model_id"],
                "openai_base_url": resolved.get("base_url") or cfg.openai_base_url,
                "openai_api_key": resolved.get("api_key") or cfg.openai_api_key,
            }
        )
    if provider == "azure_openai":
        return cfg.model_copy(
            update={
                "azure_openai_deployment": resolved["model_id"],
                "azure_openai_endpoint": resolved.get("base_url") or cfg.azure_openai_endpoint,
                "azure_openai_api_key": resolved.get("api_key") or cfg.azure_openai_api_key,
            }
        )
    return cfg


def invoke_with_hybrid_fallback(build_and_invoke, model_name=None, config=None):
    """Roda `build_and_invoke(llm)` com o provider primario
    (`config.llm_provider`, default `settings`); se falhar por
    indisponibilidade de transporte (ver `TRANSPORT_FAILURE_EXCEPTIONS`)
    e `config.llm_fallback_provider` estiver configurado, tenta
    novamente com o provider de fallback antes de desistir.

    `build_and_invoke` recebe o `BaseChatModel` ja construido e decide o
    que fazer com ele (criar um agente ReAct, chamar `.invoke()`
    diretamente, etc.) - este wrapper nao assume nada sobre a forma da
    chamada, so sobre COMO reagir a uma falha de transporte.

    Retorna `(resultado, provider_usado)`, onde `provider_usado` e o
    nome do provider que efetivamente respondeu (`"ollama"`, `"openai"`
    ou `"azure_openai"`) - util para expor no diagnostico (transparencia
    de qual provider serviu aquela chamada, ver DA-20/DiagnosisResponse).

    Levanta `ConfigurationError` se AMBOS os providers falharem (ou se
    so o primario estiver configurado e falhar).
    """
    cfg = config or settings

    primary_llm = get_chat_model(model_name=model_name, config=cfg)
    try:
        return build_and_invoke(primary_llm), cfg.llm_provider
    except TRANSPORT_FAILURE_EXCEPTIONS as primary_error:
        if not cfg.llm_fallback_provider:
            raise

        logger.warning(
            "Provider primario '%s' indisponivel (%s: %s) - tentando fallback '%s'",
            cfg.llm_provider,
            type(primary_error).__name__,
            primary_error,
            cfg.llm_fallback_provider,
        )
        fallback_cfg = cfg.model_copy(update={"llm_provider": cfg.llm_fallback_provider})
        fallback_llm = get_chat_model(model_name=model_name, config=fallback_cfg)
        try:
            return build_and_invoke(fallback_llm), fallback_cfg.llm_provider
        except TRANSPORT_FAILURE_EXCEPTIONS as fallback_error:
            raise ConfigurationError(
                f"Provider primario ('{cfg.llm_provider}') E fallback "
                f"('{cfg.llm_fallback_provider}') indisponiveis. Primario: "
                f"{primary_error}. Fallback: {fallback_error}"
            ) from fallback_error
