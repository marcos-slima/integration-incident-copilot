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

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Prefixo usado nas mensagens de erro de DA-45, para que o texto aponte
#: o campo exato a corrigir. Constante de modulo de proposito: dentro da
#: classe viraria private attr do pydantic.
_ROUTE_VALIDATION_PREFIX = "llm_route"


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

    # DA-45: rota auditada. Vazio (default) = comportamento identico ao de
    # sempre, sem validacao adicional. Preenchido = o nome precisa existir
    # em app/llm/routes.py::LLM_ROUTES e a ORIGIN REAL configurada precisa
    # ser coerente com a classe que a rota declara. Divergencia falha no
    # BOOT, nao em producao (ver _validate_llm_route).
    #
    # O MODELO NAO ENTRA AQUI. `llm_model` continua texto livre e o cliente
    # pode apontar para qualquer modelo OpenAI-compatible sem tocar em
    # codigo - e' o que "so informar" significa. O que exige PR e' adicionar
    # um FORNECEDOR, e isso e' deliberado: a tabela de rotas e' o unico lugar
    # onde a pergunta "para onde o dado pode sair?" tem resposta.
    llm_route: str = ""

    # Hybrid Inference (DA-20): se preenchido, o LLM Gateway
    # (app/llm/gateway.py::invoke_via_gateway) tenta este provider
    # automaticamente quando `llm_provider` falhar por INDISPONIBILIDADE de
    # transporte (Ollama fora do ar, timeout de rede) - nao para erros de
    # aplicacao (JSON malformado, por exemplo), que continuam subindo
    # normalmente. Vazio (default) = sem fallback, comportamento identico ao
    # de antes desta fase.
    #
    # DA-45: 'ollama' passou a ser aceito aqui. Antes o Literal era
    # ["openai","azure_openai",""] - o que tornava INEXPRESSIVEL o pedido
    # mais comum de cliente, "cloud primario, local no fallback" (e o
    # inverso, "local primario, cloud no fallback", que so funcionava por
    # causa do default llm_provider=ollama). A direcao cloud->local e' a
    # mais desejada porque mantem o dado dentro da infra do cliente: quando
    # o cloud cai, o diagnostico degrada para o modelo local em vez de ficar
    # indisponivel. Nao ha risco de seguranca em adicionar -
    # resolve_provider_origin ja resolvia 'ollama' para o loopback, que fica
    # FORA da allowlist de DA-43 (que so restringe origin remota).
    llm_fallback_provider: Literal["ollama", "openai", "azure_openai", ""] = ""

    # DA-2 manda seed=42 para garantir determinismo, e continua sendo o
    # default (ver os testes test_determinism_cross_provider.py, que
    # asseguram seed=42 em ollama E em openai).
    #
    # Porem `seed` nao faz parte do contrato minimo da API OpenAI: o
    # endpoint OpenAI-compatible do Gemini responde 400 "Unknown name
    # \"seed\": Cannot find field", e alguns gateways self-hosted (vLLM/LM
    # Studio) tambem. Desligar e' a unica forma de falar com eles - nao ha
    # fallback, o parametro e' rejeitado na request INTEIRA. Perde-se
    # determinismo no provedor que nao o suporta, o que e' inevitavel: sem
    # o parametro, o provedor decide a sampled seed.
    #
    # DA-45: isto era um booleano GLOBAL para um fato que e' POR DESTINO,
    # que e' a categoria errada. Desligar aqui para atender o Gemini tirava
    # o determinismo de DA-2 de TODOS os outros providers - foi o que
    # obrigou o eval a passar LLM_SEND_SEED=0 no processo inteiro, trocando
    # um problema local por uma perda global. Agora e' tri-state:
    #   None  (default) - consulta app/llm/capabilities.py, que decide por
    #                     ORIGIN real (mesmo principio de DA-43: o fato e'
    #                     do destino, nao do rotulo 'openai')
    #   True  - envia sempre (override explicito)
    #   False - nunca envia (override explicito)
    # O override sempre vence: quem opera o gateway conhece o gateway dele
    # melhor do que uma tabela versionada junto com o codigo.
    llm_send_seed: bool | None = None

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

    # DA-39: politica de data sovereignty para o AI Gateway.
    # 'strict' (default): dado confidencial so pode ir para providers locais
    #   (ollama). Comportamento original, adequado para ambientes on-premise.
    # 'cloud_with_dlp': dado confidencial pode ir para cloud providers
    #   (openai/azure_openai) DESDE QUE PII ja tenha sido redacted antes de
    #   chegar ao gateway (garantido por redact_pii_deep em nodes.py).
    #   Use em deploys Kyma/cloud onde Ollama local nao esta disponivel.
    # Literal, e nao str: um valor invalido tem que falhar no BOOT, nunca
    # ser interpretado como permissivo. O teste
    # test_modo_sovereignty_invalido_falha_no_boot trava isso - antes
    # disso, um typo em 'local_only' era tratado como cloud_with_dlp
    # pelo `else` do gateway, ou seja, o typo DESLIGAVA a protecao.
    data_sovereignty_mode: Literal["strict", "cloud_with_dlp"] = "strict"
    # B-04: classificacao de incidentes SEM dado real de conector (so texto
    # do usuario). 'confidential' (default conservador): em modo 'strict'
    # nao vai para LLM cloud e, com WEB_SEARCH_POLICY=public_only, nao sai
    # para busca web. 'public' restaura o comportamento anterior.
    sensitivity_default: Literal["confidential", "public"] = "confidential"

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

    # SAP Process Orchestration / Process Integration, on-premise
    # (app/connectors/po_connector.py, DA-56) - middleware A2A/B2B muito
    # adotado em LATAM/Europa, onde incidentes de integracao nascem
    # Messages FAILED/HOLDING. Autenticacao NATIVA do PO/PI e' Basic Auth
    # (usuario/senha do stack ABAP): nao existe OAuth2 no PO/PI. O modo
    # OAuth2 existe para o caso de um API Management na FRENTE dele
    # reescrever a autenticacao (ver `po_auth_mode`).
    #
    # A URL nunca deve apontar direto para o PO/PI em producao - o
    # conector e'agnesico quanto a forma de exposicao (proxy/WAF, SAP Web
    # Dispatcher, ADC, APIM): informe o endpoint do FACHADA, e ele fala
    # com o PO/PI por tras. `po_base_url` vazio = modo demo/mock, mesmo
    # criterio dos demais conectores.
    po_base_url: str = ""  # ex: https://wd-dmz.corp.example/po  (fachada, nao o PO/PI)
    po_username: str = ""  # Basic Auth nativo (padrao)
    po_password: str = ""
    # "basic" = padrao nativo do PO/PI; "oauth2" = APIM na frente
    # traduzindo Basic -> OAuth2 Client Credentials (mesmo padrao de
    # `odata_connector`/`ariba_connector`, por isso a reutilizacao).
    po_auth_mode: Literal["basic", "oauth2"] = "basic"
    po_oauth_token_url: str = ""  # obrigatorio quando po_auth_mode="oauth2"
    po_oauth_client_id: str = ""
    po_oauth_client_secret: str = ""

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
    # P0.2: politica de egress da busca web, aplicada ALEM de web_search_enabled
    # (que continua sendo o interruptor principal, default off):
    #   disabled    - nunca executa
    #   approved    - executa com query sanitizada (comportamento anterior)
    #   public_only - so executa se classify_sensitivity() == "public"
    #                 (sem dado real de conector no estado)
    web_search_policy: Literal["disabled", "approved", "public_only"] = "approved"

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

    # DA-54 — login de sessao para a UI web (POST /auth/login).
    # Formato de web_ui_users (uma ou mais entradas separadas por
    # virgula): "usuario:pbkdf2_sha256$iteracoes$salt_hex$hash_hex".
    # Vazio (default) = login FECHADO: /auth/login sempre responde 401
    # e a UI nao consegue autenticar por sessao — fail-closed, no
    # mesmo espirito de DA-18 (nunca "modo aberto" silencioso).
    web_ui_users: str = ""
    # Segredo HMAC dos cookies de sessao. Vazio = gerado no startup
    # com WARNING no log (padrao DA-18): valido ate o restart seguinte.
    session_secret: str = ""
    session_ttl_hours: int = 8
    # True em producao atras de TLS (Kyma, DA-24): marca o cookie como
    # Secure. Local em HTTP puro deixa False, senao o browser descarta.
    session_cookie_secure: bool = False
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

    # DA-43: para ONDE um incidente CONFIDENCIAL pode sair, como lista
    # separada por virgula de ORIGINS normalizadas. Vazio (default) =
    # NENHUM cloud recebe dado confidential; so providers locais.
    #
    # A unidade de governanca e' a ORIGIN, nao o nome do provider: 'openai'
    # e' so um rotulo - com OPENAI_BASE_URL vazio o dado vai para
    # api.openai.com, com ela preenchida pode ir para vLLM, Groq, Gemini,
    # LiteLLM. Um questionario de seguranca de cliente pergunta "para onde vai
    # meu dado?", e a resposta e' uma URL.
    #
    # Fail-closed: vale SOMENTE junto com data_sovereignty_mode =
    # "cloud_with_dlp". Em "strict" (default), a allowlist e' ignorada e nada
    # confidencial sai da maquina. Entradas nao parseaveis nunca AMPLIAM a
    # permissao (ver _allowed_origins em app/llm/gateway.py). A matriz de
    # decisao em vigor fica exposta em GET /llm/policy.
    confidential_allowed_origins: str = ""

    # ServiceNow - conector opcional para cenarios que envolvem ITSM
    # nao-SAP (ver app/connectors/servicenow_connector.py); se
    # servicenow_instance_url ficar vazio, o conector roda em modo
    # demo (mock), do mesmo jeito que os conectores SAP
    servicenow_instance_url: str = ""
    servicenow_username: str = ""
    servicenow_password: str = ""

    # A2A (Agent2Agent) - camada de interoperabilidade externa,
    # ver app/a2a/ e docs/a2a-interoperability-layer.md.
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

    # Admin (DA-46/47/48) - chave DEDICADA para as rotas /admin/* e as
    # paginas da UI admin (registro de modelos, credenciais
    # criptografadas, metering). Mesma semantica DA-18: vazia aqui NAO
    # significa autenticacao desabilitada - app/admin/security.py gera
    # uma chave aleatoria efemera no startup (warning no log). Nao
    # reaproveita api_key de proposito: o blast radius de um vazamento
    # na superficie admin nao deve se confundir com /diagnose/A2A/MCP.
    admin_api_key: str = ""

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

    # Persistência de incidentes (Fase 1 Observabilidade Grafana)
    # Opt-in: vazio = sem PostgreSQL (app sobe normalmente sem DB).
    # Formatos aceitos:
    #   postgresql+asyncpg://user:pass@host:5432/dbname  (async, recomendado)
    #   postgresql://user:pass@host:5432/dbname          (convertido automaticamente)
    # TimescaleDB é 100% compatível: mesmo driver, mesma URL.
    # Após configurar, aplique as migrations com:
    #   alembic upgrade head
    database_url: str = ""

    # Registro de modelos em DB (DA-46/48) - opt-in. Default OFF: o
    # runtime continua 100% dirigido pelo .env (config.py Settings) -
    # nenhum teste/nova feature quebra por causa do registro vazio.
    # `llm_registry_db=true` muda get_chat_model (app/llm/factory.py)
    # para resolver modelo, base_url e credencial a partir do registro
    # (app/admin/) em vez do .env - FAIL-CLOSED: registro vazio para a
    # origem em uso rejeita a chamada (ConfigurationError), nunca cai
    # silenciosamente de volta para o .env. As chaves dos provedores
    # ficam cifradas (DA-47) na tabela llm_credentials.
    llm_registry_db: bool = False

    # Master key Fernet (DA-47) para cifrar/decifrar credenciais dos
    # provedores em repouso no registro (llm_credentials). UNICO
    # segredo que continua no .env depois que as chaves dos provedores
    # migram para o banco. Obrigatoria quando:
    #   - llm_registry_db=true (runtime precisa decifrar para chamar o LLM)
    #   - qualquer operacao de escrita/leitura de credencial no /admin
    # Vazia nesses modos = falha explicita (nao gera key nova em runtime:
    # uma key nova tornaria indecifravel tudo o que ja esta no banco).
    llm_credentials_master_key: str = ""

    # Metering de tokens reais (DA-48): captura `usage` nas respostas
    # LLM (token_usage do OpenAI-compatible / prompt_eval_count.eval_count
    # do Ollama) em app/llm/gateway.py e persiste na tabela llm_usage
    # quando DATABASE_URL estiver configurada. Independente do modo
    # registry (o registro dirige o runtime; o metering observa os dois).
    # Best-effort idêntico ao record_incident: falha de DB nunca quebra
    # o diagnostico.
    metering_enabled: bool = True

    # Observabilidade Prometheus (Fase 2 Observabilidade Grafana)
    # Opt-in: false (default) = sem /metrics endpoint, sem import de
    # prometheus_client. True = expõe /metrics com métricas HTTP padrão
    # (prometheus-fastapi-instrumentator) + métricas de negócio iic_*.
    # Requires: pip install prometheus-fastapi-instrumentator
    prometheus_enabled: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # nao quebra se o .env tiver variaveis extras
    )

    @property
    def langfuse_configured(self) -> bool:
        """True somente com AS DUAS chaves do Langfuse presentes.

        Existe como property (e nao como campo) porque e' uma pergunta
        sobre configuracao, nao um valor que o cliente informe. Os
        chamadores checam `settings.langfuse_configured` antes de
        inicializar o SDK e antes de confiar em `trace_id` vindo do
        cliente (app/main.py, app/agent/nodes.py, app/agent/graph.py).

        Exige PUBLIC e SECRET: o SDK falha sem as duas, e uma so daria
        tracing autenticado pela metade - pior do que nenhum tracing.
        """
        return bool(self.langfuse_public_key.strip() and self.langfuse_secret_key.strip())

    # ------------------------------------------------------------------
    # DA-45: validacao de rota no BOOT
    # ------------------------------------------------------------------
    @model_validator(mode="after")
    def _validate_llm_route(self) -> "Settings":
        """Falha no boot se `llm_route` nao for coerente com a configuracao.

        Mesma politica dos `Literal` acima: valor invalido e' recusado na
        construcao do Settings, nunca interpretado como permissivo. A
        validacao nao pode ser delegada ao `Literal` porque `llm_route` e'
        texto livre - e' o NOME de uma entrada auditada em
        app/llm/routes.py, e o nome precisa ser resolvido contra ela.

        Tres recusas:
        1. rota inexistente;
        2. `llm_provider` explicitado em conflito com o provider da rota;
        3. ORIGIN REAL incoerente com a classe declarada pela rota (rota
           de laboratorio apontando para a internet, ou rota corporativa
           apontando para loopback).

        O MODELO nunca entra aqui: `llm_model` e' texto livre de
        proposito - e' o cliente que informa qual modelo usa.
        """
        if not self.llm_route:
            return self

        # Import lazy: app.llm.routes puxa capabilities/origins, que nao
        # importam config no nivel de modulo. Mesmo padrao de
        # nodes.py::_web_search_allowed.
        from app.llm.origins import is_loopback_origin, resolve_provider_origin
        from app.llm.routes import get_route, known_routes

        route = get_route(self.llm_route)
        if route is None:
            opcoes = ", ".join(sorted(known_routes()))
            raise ValueError(
                f"{_ROUTE_VALIDATION_PREFIX}='{self.llm_route}' nao e uma rota "
                f"conhecida. Rotas auditadas: {opcoes}. "
                f"Adicionar um fornecedor novo e' uma entrada em "
                f"app/llm/routes.py::LLM_ROUTES (uma DA) - de proposito, "
                f"porque e' la que a pergunta 'para onde o dado pode sair?' "
                f"tem resposta. O MODELO, esse, e' texto livre: use "
                f"LLM_MODEL sem mudar codigo."
            )

        # O provider da rota e' a fonte da verdade quando llm_provider nao
        # foi explicitado. Conflito explicito, esse sim, e' recusado - dois
        # valores dizendo coisas diferentes nao tem um "certo".
        if "llm_provider" in self.model_fields_set and self.llm_provider != route.provider:
            raise ValueError(
                f"{_ROUTE_VALIDATION_PREFIX}='{self.llm_route}' exige "
                f"llm_provider='{route.provider}', mas llm_provider="
                f"'{self.llm_provider}' foi explicitado. Os dois estao em "
                f"conflito - remova o llm_provider explicito e deixe a rota "
                f"definir, ou escolha a rota que combina com ele."
            )
        self.llm_provider = route.provider

        origin = resolve_provider_origin(route.provider, self)
        if not route.matches_destination_class(origin):
            foi = "loopback" if is_loopback_origin(origin) else "remota"
            exigia = "loopback" if route.require_loopback else "origin remota"
            where = f"'{origin}'" if origin else "(vazio)"
            raise ValueError(
                f"{_ROUTE_VALIDATION_PREFIX}='{self.llm_route}' declara "
                f"destino '{route.destination}' e exige {exigia}, mas a "
                f"configuracao efetiva resolve para a origin {where} "
                f"({foi}). Divergencia entre o rotulo auditado e o destino "
                f"real nao sobe para producao."
            )
        return self


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
