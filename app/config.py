"""Configuracao centralizada do SAP Integration Copilot.

Toda configuracao (URLs, modelos, credenciais) vem daqui - nunca
hardcoded espalhado pelo codigo. Le do .env automaticamente.

Para VER exatamente o que esta configurado agora (sem precisar ler
codigo Python), rode:

    uv run python -m app.config

Isso imprime a configuracao efetiva (valores do .env + defaults),
mascarando senhas/chaves - util pra depurar "por que esta apontando
pro lugar errado" sem depender de mais ninguem.
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # LLM provider - "ollama" (default, local-first, sem custo de API)
    # ou "openai"/"azure_openai" (para clientes que ja tem essa assinatura,
    # ou como fallback de capacidade quando o hardware local nao aguenta
    # um modelo maior). Ver docs/ARCHITECTURE.md e a Decisao de
    # Arquitetura #10 no README sobre por que isso e plugavel em vez de
    # hardcoded: o modelo de negocio do projeto (viabilizar IA para quem
    # nao pode/nao quer pagar SAP AI Core) exige rodar tanto 100% local
    # quanto, quando fizer sentido para o cliente, sobre um provedor que
    # ele ja tenha contratado - sem reescrever o grafo.
    llm_provider: Literal["ollama", "openai", "azure_openai"] = "ollama"

    # Hybrid Inference (DA-20): se preenchido, o LLM Gateway
    # (app/llm/factory.py::invoke_with_hybrid_fallback) tenta este
    # provider automaticamente quando `llm_provider` falhar por
    # INDISPONIBILIDADE de transporte (Ollama fora do ar, timeout de
    # rede) - nao para erros de aplicacao (JSON malformado, por
    # exemplo), que continuam subindo normalmente. Vazio (default) =
    # sem fallback, comportamento identico ao de antes desta fase.
    # Exige as credenciais do provider de fallback configuradas
    # normalmente (OPENAI_API_KEY ou AZURE_OPENAI_*, conforme o caso).
    llm_fallback_provider: Literal["openai", "azure_openai", ""] = ""

    # AI Gateway v1 (DA-26): teto de custo estimado (USD) por chamada
    # LLM, verificado ANTES da chamada (heuristica de tokens x tabela
    # de preco aproximada, nao cobranca real) - ver app/llm/gateway.py.
    # 0.50 e deliberadamente permissivo (nao quebra uso normal); existe
    # para pegar um caso patologico (prompt gigantesco por engano),
    # nao para orcamento fino de producao.
    llm_gateway_max_cost_usd: float = 0.50

    # Avaliacao externa (curto prazo, item 4): timeout explicito por
    # chamada LLM - antes disso, nenhum provider (ChatOllama/ChatOpenAI/
    # AzureChatOpenAI, ver app/llm/factory.py::get_chat_model) tinha
    # limite de tempo configurado, entao um Ollama travado (nao caido -
    # caido ja e tratado por TRANSPORT_FAILURE_EXCEPTIONS/circuit
    # breaker) prenderia a requisicao HTTP indefinidamente. 90s cobre
    # modelos densos grandes rodando em CPU (ver DA-29:
    # docs/RERANKER_BENCHMARK.md tem uma nota de latencia real deste
    # tipo de hardware) sem deixar uma trava real escapar sem limite.
    llm_request_timeout_seconds: float = 90.0

    # Avaliacao externa (curto prazo, item 4): timeout GLOBAL de todo o
    # pipeline de diagnostico (run_diagnosis - retrieval + GraphRAG +
    # 1-2 chamadas LLM do ReAct + relatorio), nao so de uma chamada LLM
    # isolada. Runa via asyncio.wait_for em torno da invocacao sincrona
    # do grafo (ver app/agent/graph.py::run_diagnosis) - protege contra
    # a SOMA de varias etapas lentas (nao so uma travada), que o
    # timeout por chamada LLM sozinho nao cobre.
    diagnosis_timeout_seconds: float = 180.0

    # AI Gateway v1 (DA-26): circuit breaker por provider - depois de
    # N falhas de transporte CONSECUTIVAS, o provider fica "aberto" por
    # um cooldown (chamadas seguintes pulam direto pro proximo provider
    # permitido, sem esperar timeout de novo). In-memory, por processo
    # - ver nao-objetivo em app/llm/gateway.py (nao compartilhado entre
    # replicas Kyma).
    llm_gateway_circuit_failure_threshold: int = 3
    llm_gateway_circuit_cooldown_seconds: float = 30.0
    # Backoff exponencial antes de abrir o circuit (DA-30): cada falha
    # de transporte espera min(base * 2^tentativa, max) segundos antes de
    # tentar o proximo provider. Jitter aleatorio de +/- 20% do delay
    # calculado evita thundering herd em deploy multi-instancia.
    # 0.0 desabilita o backoff (util em testes de velocidade).
    llm_gateway_backoff_base_seconds: float = 0.5
    llm_gateway_backoff_max_seconds: float = 8.0

    # DA-33: Rule Engine deterministico — avaliado ANTES do LLM para
    # incidentes conhecidos (OAuth expirado, material lock, IDoc 51, etc.).
    # Desabilitar so para testes que precisam forcas o caminho LLM.
    rule_engine_enabled: bool = True

    # Avaliacao externa (medio prazo, item 3): mesmo mecanismo do
    # circuit breaker do AI Gateway acima (app/circuit_breaker.py),
    # agora tambem para os conectores HTTP reais (SAP OData/CAP/API
    # Management, ServiceNow, Salesforce, Workday, Ariba - ver
    # app/connectors/base.py::circuit_breaker_guard). Falha consecutiva
    # aqui = erro de REDE (timeout, conexao recusada), nao um 404/400
    # de negocio (identificador nao encontrado nao significa que o
    # sistema esta fora do ar).
    connector_circuit_failure_threshold: int = 5
    connector_circuit_cooldown_seconds: float = 30.0

    # Ollama (default local-first)
    ollama_host: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen3-coder-next:latest"
    embedding_model: str = "nomic-embed-text"

    # OpenAI / compativel com OpenAI (inclui endpoints locais tipo
    # vLLM/LM Studio que implementam a mesma API) - so relevante se
    # llm_provider="openai"
    openai_api_key: str = ""
    openai_base_url: str = ""  # vazio = API oficial da OpenAI

    # Azure OpenAI - so relevante se llm_provider="azure_openai"
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = "2024-10-21"

    # SAP RFC (conexao direta via pyrfc) - so relevante para
    # RFCConnector(use_real=True); modo demo/mock (default) nao le
    # nenhum destes campos
    sap_ashost: str = ""
    sap_sysnr: str = "00"
    sap_client: str = "100"
    sap_user: str = ""
    sap_password: str = ""

    # OData/CPI real (app/connectors/odata_connector.py) - OAuth2
    # client_credentials contra o token endpoint do CPI/Integration
    # Suite, seguido de GET no servico OData real. Vazio (default) =
    # modo demo/mock, mesmo criterio dos demais conectores.
    odata_service_url: str = ""
    odata_oauth_token_url: str = ""
    odata_client_id: str = ""
    odata_client_secret: str = ""

    # Salesforce (app/connectors/salesforce_connector.py) - OAuth2
    # Client Credentials Flow (Connected App) + SOQL via REST API.
    # Representa o cenario de referencia Salesforce<->SAP.
    salesforce_instance_url: str = ""
    salesforce_client_id: str = ""
    salesforce_client_secret: str = ""
    salesforce_api_version: str = "v61.0"

    # Workday (app/connectors/workday_connector.py) - OAuth2 Client
    # Credentials Grant + REST API. Representa o cenario de referencia
    # SuccessFactors<->Workday (replicacao de dados de funcionario).
    workday_tenant: str = ""
    workday_rest_base_url: str = ""  # ex: https://wd2-impl-services1.workday.com
    workday_client_id: str = ""
    workday_client_secret: str = ""

    # SAP SuccessFactors Employee Central (app/connectors/successfactors_connector.py)
    # OAuth2 Client Credentials via SAP BTP/IAS + OData v2 PerPerson API.
    # Representa o cenario de referencia SuccessFactors<->S/4HANA (replicacao
    # de funcionario falhando por divergencia de dados ou bloqueio MDI).
    sfsf_base_url: str = ""  # ex: https://<tenant>.successfactors.com
    sfsf_oauth_token_url: str = ""  # ex: https://<tenant>.auth.us10.hana.ondemand.com/oauth/token
    sfsf_client_id: str = ""
    sfsf_client_secret: str = ""

    # SAP Ariba / Business Network (app/connectors/ariba_connector.py) -
    # OAuth2 Client Credentials contra o token endpoint da Ariba, REST
    # sobre o status de pedido de compra na rede. Representa o cenario
    # de referencia SAP Ariba<->S/4HANA.
    ariba_oauth_token_url: str = ""
    ariba_base_url: str = ""
    ariba_client_id: str = ""
    ariba_client_secret: str = ""

    # SAP CAP (app/connectors/cap_connector.py) - OData v4 (protocolo
    # default de qualquer servico CAP, caminho recomendado pelo Clean
    # Core) + XSUAA (OAuth2 Client Credentials, Basic Auth no token
    # endpoint - client vinculado a um subaccount/service instance do
    # BTP, diferente de um client OAuth2 "solto"). Vazio (default) =
    # modo demo/mock, mesmo criterio dos demais conectores.
    cap_service_url: str = ""
    cap_xsuaa_token_url: str = ""
    cap_client_id: str = ""
    cap_client_secret: str = ""

    apim_analytics_url: str = ""

    # Web search fallback (v1.1+)
    web_search_enabled: bool = (
        False  # opt-in explícito — evita exfiltração de dados do incidente para a web
    )
    web_search_threshold: float = 0.6

    # Avaliacao externa (nova revisao, P1 - "Agente ReAct pode vazar
    # dados na web"): create_react_agent (app/agent/nodes.py) nunca
    # tinha um recursion_limit explicito - o default do LangGraph e
    # 25 "super-steps" (cada rodada agente->tool->agente conta varios),
    # caro/lento sem necessidade real para um agente com um unico tool
    # (busca web). 8 permite ~3-4 rodadas de busca antes de forcar o
    # agente a concluir, sem abrir espaco pra loop indefinido custando
    # tempo/dinheiro de LLM.
    react_agent_recursion_limit: int = 8

    # Auth (opcional por padrao - se vazio, uma chave aleatoria por
    # processo e gerada no startup, ver _ensure_api_keys_configured em
    # app/main.py; para exigir chave EXPLICITA e travar o startup caso
    # contrario, ver require_auth abaixo)
    api_key: str = ""
    # Avaliacao externa (curto prazo, item 1): "Auth obrigatoria em modo
    # producao". Com require_auth=true, o lifespan do FastAPI (app/main.py)
    # recusa subir se api_key/a2a_api_key/event_mesh_api_key estiverem
    # vazios - em vez de gerar uma chave efemera por processo (o
    # comportamento default, pensado pra "clone e rode" sem config
    # nenhuma). A chave efemera muda a cada restart e so aparece num log
    # de warning - adequado pra dev local, nao pra um deploy que um
    # operador espera acessar de forma estavel/documentada.
    require_auth: bool = False
    apim_oauth_token_url: str = ""
    apim_client_id: str = ""
    apim_client_secret: str = ""

    # Qdrant
    qdrant_url: str = "http://127.0.0.1:6333"

    # Neo4j / GraphRAG (app/rag/graph_store.py) - desligado por default
    # (graph_rag_enabled=False). Ligar exige DUAS coisas: (1) subir o
    # Neo4j real (`docker compose --profile graphrag up -d neo4j`) e
    # (2) GRAPH_RAG_ENABLED=true no .env. O codigo de escrita/consulta
    # ao grafo ja existe e e testado (com driver fake, ver
    # tests/test_graph_store.py) - nao ha nada para "descomentar" no
    # Python, so essa flag + a infra de fato existir.
    graph_rag_enabled: bool = False
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    # Redis - opcional (app/a2a/task_store.py). Vazio (default) =
    # tasks A2A ficam so em memoria (comportamento historico, perdido
    # a cada restart do processo) - mesmo principio de "clone e rode"
    # sem infra obrigatoria usado no GraphRAG acima. Configurado =
    # persistencia sobrevive a restart. Avaliacao externa (medio
    # prazo, item 2): "Persistencia de tasks A2A (Redis/SQLite)".
    redis_url: str = ""

    # Langfuse - opcional; se as chaves ficarem vazias o SDK nao envia
    # trace nenhum (nao quebra), entao rodar sem observabilidade
    # completa (ex: docker-compose.yml deste repo, que nao sobe o stack
    # completo do Langfuse) continua funcional
    langfuse_host: str = "http://127.0.0.1:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # ServiceNow - conector opcional para cenarios que envolvem ITSM
    # nao-SAP (ver app/connectors/servicenow_connector.py); se
    # servicenow_instance_url ficar vazio, o conector roda em modo
    # demo (mock), do mesmo jeito que os conectores SAP
    servicenow_instance_url: str = ""
    servicenow_username: str = ""
    servicenow_password: str = ""

    # A2A (Agent2Agent) - camada de interoperabilidade externa,
    # ver app/a2a/ e docs/proposals/a2a-interoperability-layer.md.
    # a2a_api_key vazio aqui (default) NAO significa autenticacao
    # desabilitada (DA-18): app.main._ensure_api_keys_configured gera
    # uma chave aleatoria no startup se esta continuar vazia. Vazio so
    # significa "sem chave fixa configurada pelo operador".
    a2a_api_key: str = ""

    # DA-30: URL base publica deste agente, usada para montar a URL
    # absoluta no Agent Card (spec A2A 0.3 exige URL absoluta, nao
    # path relativo). Ex: "https://copilot.empresa.com". Vazio (default)
    # mantém o path relativo "/a2a" no card — ok para desenvolvimento.
    a2a_base_url: str = ""

    # Event Mesh (DA-23) - ingestao orientada a evento: POST
    # /events/incident recebe um envelope CloudEvents (formato usado
    # pelo SAP Event Mesh em modo REST/Webhook push subscription) e
    # dispara run_diagnosis() automaticamente, sem chamada manual a
    # /diagnose. Chave DEDICADA (nao reaproveita api_key/a2a_api_key) -
    # isola o blast radius de um webhook vazado dos outros dois canais.
    # Vazia aqui (default) NAO significa autenticacao desabilitada -
    # mesmo padrao DA-18: app.main._ensure_api_keys_configured gera uma
    # chave aleatoria no startup se continuar vazia.
    event_mesh_api_key: str = ""

    # AMQP 1.0 — Solace Cloud / SAP Advanced Event Mesh (DA-32)
    # Consumidor assíncrono de eventos de incidente via fila AMQP.
    # AMQP_ENABLED=false desabilita sem remover a dependência aiormq.
    amqp_enabled: bool = False
    amqp_host: str = ""
    amqp_port: int = 5671
    amqp_username: str = ""
    amqp_password: str = ""
    amqp_queue: str = "integration/incidents"
    amqp_prefetch: int = 1
    amqp_reconnect_delay: int = 5

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # nao quebra se o .env tiver variaveis extras
    )


settings = Settings()


def _mask(value: str) -> str:
    if not value:
        return "(vazio)"
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}...{value[-3:]}"


_SENSITIVE_KEYWORDS = ("password", "secret", "key")


def _print_effective_config() -> None:
    print("Configuracao efetiva (env_file=.env + defaults do codigo):\n")
    for field_name in settings.__class__.model_fields:
        value = str(getattr(settings, field_name))
        is_sensitive = any(kw in field_name for kw in _SENSITIVE_KEYWORDS)
        display = _mask(value) if is_sensitive else (value or "(vazio)")
        print(f"  {field_name:22} = {display}")
    print(
        "\nSe algum valor nao bater com o esperado, confira o arquivo "
        "'.env' na raiz do projeto - essa e a unica fonte que este "
        "comando le, alem dos defaults acima."
    )


if __name__ == "__main__":
    _print_effective_config()
