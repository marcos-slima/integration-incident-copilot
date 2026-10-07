# CLAUDE.md — Integration Incident Copilot

Contexto de trabalho para o Claude Code CLI. Leia antes de qualquer edição.

---

## O que é este projeto

Assistente de IA para diagnóstico de incidentes de integração empresarial.
Multi-vendor por design: SAP (OData, RFC, CPI, BTP) é um conector entre
vários — ServiceNow, Salesforce, Workday, Ariba, CAP, APIManagement.
Portfólio da trilha SAP Architect → AI Architect (Marcos Lima).

---

## Stack

| Camada | Tecnologia |
|---|---|
| API | FastAPI + Pydantic |
| Orquestração | LangGraph (`app/agent/graph.py`) |
| LLM | Ollama local (default) → OpenAI / Azure como fallback (DA-20/26) |
| Modelo canônico | `qwen3-coder-next:latest` (MoE 80B/3B ativo, 262K ctx; 10/10 no promptfoo da Fase 12 — substituiu `qwen2.5-coder:32b`, ver DA-4/8 + Fase 12). **Ressalva (validação 2026-10-07, M-21):** 4 dos 11 casos atuais são resolvidos pelo rule engine e não medem o LLM (`metadata.path` no YAML), e o modelo reprovou o caso "queue manager" do compare — o placar precisa ser re-medido |
| RAG | LangChain + Qdrant (hybrid dense+sparse BM25, fusão RRF) |
| Reranker | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (benchmark DA-29; não usar ms-marco-L6) |
| GraphRAG | Neo4j (`app/rag/graph_store.py`), opt-in via `GRAPH_RAG_ENABLED=true` |
| Observabilidade | Langfuse (opcional; tracing completo quando `LANGFUSE_*` configurado) |
| Gerenciador | `uv` — **não usar pip/poetry diretamente** |
| Testes | pytest + pytest-asyncio (modo strict) |
| Linting | ruff + ruff-format (pre-commit automático) |

---

## Ambiente local

```bash
# Ativar venv em qualquer shell
PATH="$PWD/.venv/bin:$PATH"

# Rodar testes unitários (sem infra)
uv run pytest tests/ -m "not integration" -v

# Rodar testes de integração (precisa Ollama + Qdrant rodando)
uv run pytest tests/ -v

# Servidor de desenvolvimento
uv run uvicorn app.main:app --reload

# Ingestão de documentos
uv run python -m app.rag.ingest --target incidents --reset
```

**Infraestrutura local** — tudo vem do `docker-compose.yml` **deste repo**.
Não há diretório de infra externo; um stack pessoal que existiu foi
removido e o compose passou a ser a única fonte:

```bash
docker compose --profile observability up -d qdrant postgres grafana redis
docker compose --profile graphrag up -d neo4j      # opt-in
```

- Qdrant → `localhost:6333` (ou `:6335` se `QDRANT_HOST_PORT` no `.env`)
- Postgres → `localhost:5432` (perfil `observability`)
- Neo4j → `localhost:7474` (perfil `graphrag`, opt-in)
- Grafana → `localhost:3001` (perfil `observability`)
- Ollama → `localhost:11434` — **nativo**, `/usr/local/bin/ollama`, não em
  container. O serviço `ollama` do compose existe (perfil
  `container-ollama`) mas é opt-in; o compose aponta para o host via
  `host.docker.internal`.

> **`.env` é por modo, e o erro é fácil.** Os dois modos usam o mesmo
> compose; muda só onde o processo Python roda. No modo **nativo** o `.env`
> precisa de hosts de loopback — `DATABASE_URL=…@127.0.0.1:5432/iic` — e
> `postgres` **não resolve na máquina** (é nome de serviço na rede do
> compose). No modo **container** é o inverso: o compose injeta `postgres`,
> `qdrant` e `neo4j` por default em `x-common-env`, então apontar o `.env`
> para `127.0.0.1` quebra o container. Para apontar o container a um banco
> externo, use `CONTAINER_DATABASE_URL` / `CONTAINER_NEO4J_URI`, que não
> colidem com as variáveis do modo nativo.
>
> O `NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD` faltavam do `x-common-env`
> até este estado: o serviço `neo4j` existia no perfil `graphrag` mas a API
> nunca recebia as credenciais, então o GraphRAG não tinha como alcançar o
> banco nem no modo container.
>
> `NEO4J_AUTH` do Neo4j só vale na **primeira inicialização** do volume.
> Trocar a senha no `.env` depois não troca a senha do banco — precisa
> remover o volume `integration-incident-copilot_neo4j_data`. Sem
> `NEO4J_PASSWORD` no `.env`, o serviço sobe com o placeholder
> `neo4j/REQUIRED_SET_IN_ENV` e falha em `verify_connectivity()`.
>
> **`GRAPH_RAG_ENABLED=false` é o default** (`app/config.py`) e é a flag
> real — o `CLAUDE.md` antigo citava uma flag USE_GRAPH_RAG, que não existe.
>
> **Dois Qdrant podem coexistir na mesma máquina.** O compose deste repo
> publica em `${QDRANT_HOST_PORT:-6333}`. Se outro stack (ou um Qdrant
> standalone) já usar a `6333`, defina `QDRANT_HOST_PORT=6335` — é o que
> o `.env` local faz, e é por isso que `scripts/debug_matched_source.py`
> existia com `6335` fixado. O acervo grande (`sap_reference_library`) fica no Qdrant do outro stack, não neste repo;
> o tamanho real fica em `data/index_manifest.json` (gerado pelo ingest, validação 2026-10-07, M-23) —
> os números antigos (~767k, 28.962, 100.805) eram incompatíveis entre si.
>
> armadilha correlata: `data/.ingest_state_reference.json` é **um arquivo por
> target, sem URL dentro** (chave = `hash:filename`, `app/rag/ingest.py:167`).
> Ele marca "já processado" sem registrar *onde*. Rodar a ingestão contra
> um Qdrant e depois contra outro faz o segundo run **pular tudo** e o
> destino ficar sem os 22 GB esperados — sem erro. Trocar de destino exige
> `--reset-state` **e** gravar o estado contra a URL pretendida.

### MCP para agentes de IA (DA-19)

O servidor MCP é **Streamable HTTP**, montado em `/mcp` dentro do app
FastAPI — não é um processo stdio separado. Ele exige o mesmo `X-API-Key`
de `/diagnose` (DA-18) via `RequireApiKeyMiddleware`.

Consequência prática: `settings.api_key` é **gerada aleatoriamente no
startup** quando está vazia (`app/main.py::_ensure_api_keys_configured`).
Para um cliente MCP conseguir autenticar, `API_KEY` precisa estar
**fixada no `.env`** (ver `.env.example:120`).

Configuração por ferramenta:

| Ferramenta | Arquivo | Dialeto |
|---|---|---|
| Claude Code | `.mcp.json` | `type: http` + `${API_KEY}` |
| OpenCode | `opencode.json` | `type: remote` + `{env:API_KEY}` |
| Codex | `~/.codex/config.toml` | `[mcp_servers.copilot-mcp]` (global) |

Depois de editar qualquer um desses, **reinicie** a ferramenta — config MCP
não é hot-reload.

Requisito: `uv run uvicorn app.main:app` no ar, senão o cliente falha ao
conectar.

---

## Regras de commit (OBRIGATÓRIO)

```bash
# Pre-commit hooks: ruff, ruff-format, gitleaks, trim-whitespace
# O PATH precisa incluir o .venv para o hook encontrar ruff:
PATH="$PWD/.venv/bin:$PATH" git commit -m "..."
```

Se o hook rodar ruff e reformatar arquivos → `git add` novamente + re-commit.

**Nunca usar** valores com prefixo `sk-` em testes (dispara gitleaks).
**Nunca usar** nomes de campo como `api_key` ou `generic-api-key` em valores de teste.

---

## Estrutura de pastas relevantes

```
app/
  agent/
    graph.py        # Grafo LangGraph + run_diagnosis() — ponto de entrada
    prompts.py      # DA-53: artefato de prompt (version+digest); fonte
                    # única de persona, template e instrução de saída
    nodes.py        # Todos os nodes: connector, retrieve, diagnose, report
                    # + _assemble_evidence(), _apply_confidence_guardrails()
    rules.py        # DA-33: Rule Engine determinístico (22 regras: KNOWN_ERROR_RULES)
    escalation.py   # DA-44: sinal determinístico de escalonamento (3 tiers)
    supervisor.py   # DA-22: classifica domínio (sap/saas/generic) sem LLM
    state.py        # CopilotState (TypedDict)
  llm/
    factory.py      # DA-20: Hybrid Inference (Ollama → cloud fallback)
    gateway.py      # DA-26: AI Gateway (policy + circuit breaker + budget)
  admin/            # DA-46/47/48/49/50: registro de modelos + credenciais
                    # Fernet + metering real + catálogo de sistemas
                    # integrados (DA-49) + correlação de incidentes
                    # (correlation.py, DA-50); UI Jinja2 em /admin
                    # (models, usage, systems, incidents) + API /admin/api/*
  mcp/
    server.py       # DA-19: servidor MCP
    policy.py       # DA-27: Capability Registry (FAIL-CLOSED por default)
  contracts/        # DA-52: drift de contrato SAP
    model.py        # Contrato normalizado + fingerprint canônico
    odata.py        # Parser EDMX ($metadata) → contrato
    diff.py         # Severidade fechada: breaking/additive/cosmetic
    baseline.py     # ORM SystemContract (append-only, sem FK)
    observe.py      # probe → diff → baseline → CloudEvent
  rag/
    retriever.py    # RAG híbrido + reranker
    graph_store.py  # GraphRAG (Neo4j)
    ingest.py       # Indexação de documentos
  evaluation/
    gates.py        # DA-51: checks determinísticos de qualidade (sem LLM/infra)
    coverage.py     # DA-58: cálculo do mapa produto SAP × mecanismo
                    # (3 níveis; `unknown` ≠ `none`) — sem import de `app.*`
                    # pesado, para o gate continuar leve
  events/
    consumer.py     # DA-23: webhook CloudEvents → run_diagnosis()
  a2a/              # DA-14: Agent2Agent (JSON-RPC 2.0)
  auth.py           # DA-54: sessão da UI (login/cookie HMAC + logout/
                    # session) e DA-55: rotas públicas de ativação
                    # (/auth/verify/email, /auth/verify/phone); o login
                    # verifica .env (bootstrap) → banco (web_users ativos)
  connectors/       # 10 conectores: odata, rfc, servicenow, salesforce,
                    # workday, ariba, successfactors, cap, apimanagement,
                    # po (DA-56, SAP PO/PI on-premise)
  webusers.py       # DA-55: domínio de usuários da UI — token de e-mail
                    # (HMAC, namespace próprio), código de telefone (só o
                    # hash guardado, único-uso), status pending_email →
                    # pending_phone → active, entrega out-of-band
                    # (adaptador deliver_email/deliver_sms para SMTP/SMS)
  config.py         # Pydantic Settings — fonte única de verdade para config
  main.py           # FastAPI app, rotas, lifespan

tests/              # suite unitária + integração; o número exato muda a cada
                    # commit, então não é declarado aqui — rode
                    # `uv run pytest tests/ -m "not integration"` para o total atual.
                    # 9 deles são e2e da DA-52 e só rodam com
                    # IIC_TEST_DATABASE_URL + Postgres (job CI
                    # migrations_and_dashboards)
data/
  sample_docs/      # Base de conhecimento RAG (arquivos .md)
  eval/             # Dataset de avaliação RAG + resultados benchmark
                    # + baseline do promptfoo (DA-51), versionados
  sap_products.yaml      # DA-58: 27 linhas × 9 mecanismos (dado do mapa)
  connector_coverage.yaml # DA-58: conector -> produto + mecanismos exercitados
scripts/            # benchmark_rerankers.py, generate_reports.py,
                    # validate_dashboards.py (45 queries Grafana vs Postgres real),
                    # coverage_map.py (DA-58: gera docs/COVERAGE_MAP.md),
                    # quality_gate.py (DA-51)
docs/               # índice em README.md; ARCHITECTURE.md, GETTING_STARTED.md,
                    # TUTORIAL_ARQUETURA_DEBUG.md, TROUBLESHOOTING.md,
                    # QUALITY_GATES.md, COVERAGE_MAP.md (DA-58, GERADO),
                    # CASOS_DE_USO.md (cenários medidos por testes), CONNECTORS.md
.vscode/
  launch.json       # 13 configurações de debug prontas (graph, pytest, uvicorn)
```

---

## Decisões de Arquitetura (DAs) — resumo

> Para detalhes completos: `README.md` (seção "Decisões de Arquitetura").

| DA | O que é | Onde vive |
|---|---|---|
| DA-1 | RAG top-1 (evita mistura de contexto) | `retriever.py` |
| DA-2 | `seed=42` obrigatório para determinismo Ollama | `factory.py` |
| DA-3 | Guardrails em código, não em prompt | `nodes.py::_apply_confidence_guardrails()` |
| DA-4/8 | Comparações de modelo via promptfoo: `qwen2.5-coder:32b` ganhou do `qwen3:30b-a3b` (DA-4) e do `qwen3.6:35b-a3b` (DA-8); trocado por `qwen3-coder-next:latest` na Fase 12 (paridade 10/10) | `config.py::llm_model` |
| DA-12 | Troca final do modelo canônico para `qwen3-coder-next:latest` (MoE 80B/3B, 262K ctx) — empate técnico 10/10 com `qwen2.5-coder:32b` no promptfoo, decidida por roadmap (commit `0222b79`, sem prefixo de DA: por isso ficou anos sem registro) | `config.py::llm_model` + `README` |
| DA-32 | Consumidor AMQP 1.0 assíncrono para Solace Cloud / SAP Event Mesh (corrigido pela DA-40) | `events/amqp_consumer.py` |
| DA-34 | Conector SuccessFactors EC (OAuth2 Client Credentials + OData v2 PerPerson) | `connectors/successfactors_connector.py` |
| DA-35 | `/health` como readiness probe real (GET nos serviços) + expansão do catálogo Rule Engine | `main.py::_probe_infra_services` |
| DA-14 | Camada A2A (Agent2Agent) JSON-RPC 2.0 | `app/a2a/` |
| DA-15 | Evidence/Trust Layer determinística | `nodes.py::_assemble_evidence()` |
| DA-16 | `is_grounded` via evidence_strength (nunca autoavaliação LLM) | `nodes.py` |
| DA-17 | Fallback para reference_library quando evidência fraca | `retriever.py` |
| DA-18 | Auth X-API-Key obrigatória em `/diagnose` e `/a2a` | `main.py` |
| DA-19 | Servidor MCP (capability catalog) | `app/mcp/` |
| DA-20 | Hybrid Inference: Ollama local → cloud fallback | `llm/factory.py` |
| DA-21 | GraphRAG hardening: `(DriverError, TransientError)` vs `Neo4jError` | `rag/graph_store.py` |
| DA-22 | Multi-agent: supervisor → sap/saas/generic (sem LLM) | `agent/supervisor.py` + `graph.py` |
| DA-23 | Event Mesh via webhook CloudEvents → `run_diagnosis()` | `app/events/consumer.py` |
| DA-24 | Deploy SAP BTP Kyma Runtime | `deploy/kyma/` |
| DA-25 | Evidence/Trust Layer v2 + threshold RAG pós-reranker | `retriever.py::_evidence_admission_score()` |
| DA-26 | AI Gateway v1: policy + circuit breaker + budget | `llm/gateway.py` |
| DA-27 | Capability Registry FAIL-CLOSED | `mcp/policy.py` |
| DA-28 | GraphRAG modelo `VERIFIED_AS` + endpoint `/incidents/{id}/verify` | `rag/graph_store.py` |
| DA-29 | Benchmark rerankers → mmarco-mMiniLMv2 vence (+7pp Hit@1) | `retriever.py::RERANKER_MODEL` |
| DA-30 | PII redaction ampliado + smart log truncation + backoff exponencial | `redaction.py` |
| DA-33 | Rule Engine determinístico (pré-filtro LLM; 22 regras em `KNOWN_ERROR_RULES`) | `agent/rules.py` |
| DA-38 | `EMBEDDING_BACKEND=fastembed` para o job de avaliação RAG no CI, que não tem Ollama | `rag/retriever.py` |
| DA-39 | Política de soberania de dados no AI Gateway (`strict` / `cloud_with_dlp`) | `config.py` + `llm/gateway.py` |
| DA-40 | Migração aiormq (AMQP 0.9.1) → python-qpid-proton (AMQP 1.0), com wrapper asyncio. Diagnóstico **fora do reactor** (RQ com `REDIS_URL`, senão `ThreadPoolExecutor`); disposition só na thread do reactor via `EventInjector`; `AMQP_MAX_REDELIVERIES` → REJECTED (DMQ); timer de parada (validação 2026-10-06) | `events/amqp_consumer.py` |
| DA-41 | Circuit breaker com backend Redis compartilhado (fallback em memória sem infra obrigatória) | `circuit_breaker.py` |
| DA-42 | Escala calibrada por sigmoid para o rerank score (nenhum consumer usa o score cru) | `rag/retriever.py` + `agent/escalation.py` |
| DA-43 | Soberania de dados por origin real, fail-closed | `llm/gateway.py` + `GET /llm/policy` |
| DA-44 | Sinal determinístico de escalonamento em 3 tiers (prep. tier 3) | `agent/escalation.py` |
| DA-45 | Universalidade de provider: rota auditada + capacidades por origin + identidade de embedding | `llm/routes.py`, `llm/capabilities.py`, `llm/origins.py`, `rag/embedding_guard.py` |
| DA-46 | Registro gerenciado de modelos/credenciais por ORIGEM (LLM_REGISTRY_DB, fail-closed) | `app/admin/` (models, repository, runtime, routes, ui) |
| DA-47 | Credenciais cifradas em repouso com Fernet (master key no .env, nunca em runtime) | `app/admin/crypto.py` |
| DA-48 | Metering de tokens REAIS (usage_metadata, não estimativa) persistido best-effort | `app/llm/gateway.py` + `app/admin/metering.py` |
| DA-49 | Catálogo de sistemas integrados (`integration_systems`) na superfície admin, `connector_type` = Literal do pipeline | `app/admin/` (models, repository, routes, ui) + `alembic/004` |
| DA-50 | Correlação `incidents` ↔ catálogo por `system_key` (exato, vindo de `IncidentRequest.connector_source_system`) com fallback por `connector_type` e ambiguidade fail-closed; verificação persistida no SQL; tela `/admin/incidents` + dashboard `iic-systems` | `app/admin/correlation.py`, `app/agent/graph.py`, `app/services/incident_recorder.py::record_verification`, `app/admin/routes.py`, `scripts/validate_dashboards.py` |
| DA-51 | Quality gates: invariantes de avaliação verificadas por máquina (dataset/corpus, invariante DA-29, configs promptfoo, freshness das DAs candidatas) + **integridade da documentação** (fences, links, referências `app/x.py::símbolo`) + **alcançabilidade de conectores** (registro → Literal do pipeline → supervisor, cada Literal conferido por separado) e **cobertura da matriz de validação** (todo conector registrado tem linha em `docs/ARCHITECTURE.md`, nenhuma órfã) + migrações e 45 queries no CI | `app/evaluation/gates.py`, `scripts/quality_gate.py`, `.github/workflows/quality.yml`, `docs/QUALITY_GATES.md` |
| DA-52 | Detecção de drift de contrato SAP: probe `$metadata` (interface segregada `fetch_contract`), normalização+hash canônico, severidade fechada (breaking/additive; `cosmetic` reservado, não emitido), baseline append-only `system_contracts` (migration 005) e sinal via event mesh só em breaking | `app/contracts/` (`model`, `odata`, `diff`, `baseline`, `observe`), `app/connectors/odata_connector.py::fetch_contract`, `scripts/check_contract_drift.py` |
| DA-53 | Prompt de diagnóstico como artefato versionado: `PromptSpec` (version+digest) em módulo próprio, proveniência (`llm_model`/`prompt_version`/`prompt_digest`) na resposta, no relatório e em `incidents` (migration 006), e gate `prompt_digest_measured` amarra produção ao prompt medido | `app/agent/prompts.py`, `app/evaluation/gates.py::check_prompt_digest`, `data/eval/prompt_baseline.json` |
| DA-54 | Login de sessão para a UI web: `POST /auth/login` (usuário+senha → cookie HttpOnly assinado HMAC) como alternativa à `X-API-Key` em `/diagnose`; superfícies de máquina (MCP/A2A/events/admin) seguem só com chaves dedicadas | `app/auth.py`, `app/main.py`, `frontend/src/api/auth.ts` |
| DA-55 | Manutenção de usuários da UI pelo admin: tabela `web_users` (migration 007), ativação em duas etapas (token por e-mail enviado via `EMAIL_PROVIDER` — Mailpit/Resend —, código de telefone out-of-band até haver provedor de SMS), CRUD `/admin/api/users` + tela `/admin/users`; login verifica banco **e** `.env` (bootstrap nunca desliga) | `app/webusers.py`, `app/admin/` (models, routes, ui), `app/auth.py` (`/auth/verify/*`, login env→banco), migration 007 |
| DA-56 | Conector SAP PO/PI on-premise (`POConnector`): Basic Auth nativo contra o Message Monitor `/mdt/api/1.0/facade`, OAuth2 opcional quando há API Management na frente, agnóstico ao padrão de exposição (informe a fachada em `PO_BASE_URL`); API **não pública** e nunca validada contra PO/PI real | `app/connectors/po_connector.py`, `app/config.py` (`po_*`), matriz em `docs/ARCHITECTURE.md` |
| DA-57 | Fontes de busca web viram configuração: `web_search_sources` (uma linha por `interface_type`) substitui os dois mapas literais de `app/agent/nodes.py`; `WEB_SEARCH_POLICY=approved` passa a exigir linha habilitada — **fail-closed, sem fallback em código**. O gate `connector_reachable` ganha a **7ª superfície** (seed da migration 008) e a **8ª** (`<select name="connector_type">` de `app/admin/templates/systems.html`, que omitia `successfactors` e `po` e fazia a correlação DA-50 cair no fallback) | `app/admin/models.py` (`WebSearchSource`), `app/admin/repository.py`, `app/admin/routes.py` + `ui.py` (`/admin/api/web-search-sources`, `/admin/web-search`), `app/services/web_search_sources.py`, `app/agent/nodes.py` (`_web_search_allowed`), `app/admin/templates/systems.html`, `alembic/versions/008_*.py`, `app/evaluation/gates.py` |
| DA-58 | Mapa de cobertura produto SAP × mecanismo, **calculado** de dados versionados (27 linhas × 9 mecanismos) em vez de tabela mantida à mão: 3 níveis (`dedicated` / `generic` / `absent`) porque "cliente OData alcançaria" não é "conector de S/4HANA". Os dois eixos (capacidade do produto = afirmação sem fonte; cobertura do repo = fato verificável) são independentes, e `unknown` ≠ `none`. Gate `connector_coverage` reprova por **incoerência**, nunca por lacuna | `data/sap_products.yaml`, `data/connector_coverage.yaml`, `app/evaluation/coverage.py`, `scripts/coverage_map.py`, `docs/COVERAGE_MAP.md`, `app/evaluation/gates.py` |
| DA-59 | Conectores multi-vendor: fluxo completo do pipeline, padrão comum (`fetch(identifier) → ConnectorResult`), checklist de 9 superfícies ao adicionar conector (registry, 2 Literals, supervisor, CLI, `CONNECTOR_TYPES`, seed `web_search_sources`, formulário de sistemas, `connector_coverage.yaml`), e documento consolidado `docs/CONNECTORS.md` (reescrito em 2026-10-07; variáveis conferidas pelo gate `docs_env_vars`) | `app/connectors/__init__.py`, `app/models.py`, `app/agent/supervisor.py`, `app/agent/graph.py` (CLI), `app/admin/templates/systems.html`, `app/admin/models.py`, `data/connector_coverage.yaml`, `docs/CONNECTORS.md` |
| DA-60 | Criptografia em repouso de `evidence_json` com Fernet (`LLM_CREDENTIALS_MASTER_KEY`) + migration idempotente `009_encrypt_evidence_json.py` | `app/admin/crypto.py`, `app/services/incident_recorder.py`, `app/admin/routes.py`, `alembic/versions/009_encrypt_evidence_json.py` |

**DAs candidatas (sem implementação ainda):**
- DA-31: SAP AI Agent Hub registration (MCP + A2A) — bloqueada: exige tenant Kyma

> A DA-32 (AMQP 1.0 via Solace Cloud) **já foi entregue** (`app/events/amqp_consumer.py`,
> commit `67b78e8`). Ela ficou nesta lista até a DA-51 criar o gate
> `candidate_das_fresh`, que falha o build quando uma DA marcada como candidata
> já tem seção em `docs/ARCHITECTURE.md`.

---

### 46. Criptografia em repouso de `evidence_json` com Fernet (DA-60)

**O problema.** O campo `evidence_json` (JSONB) da tabela `incidents` armazena
estruturas sensíveis (códigos de erro, traces, paths, payloads): dados que
podem expor caminhos de sistema, credenciais em memória ou traces internos.
O field era **sem criptografia em repouso**, o que violava a política de
soberania de dados (DA-39) e não atendia ao padrão já adotado para
credenciais (DA-47/DA-48 com Fernet e `LLM_CREDENTIALS_MASTER_KEY`).

O problema foi detectado em auditoria de segurança (DATA-02), que exigiu
migration para cifrar os dados já presentes e garantir que novas gravações
sejam cifradas automaticamente.

**O caso de uso.** Aplicações com compliance rigoroso exigem que dados de
incidentes, mesmo non-PII, sejam armazenados com criptografia em repouso. O
`evidence_json` é um dos campos mais sensíveis porque contém o **contexto
completo** do diagnóstico: traces, códigos de erro, payloads. Um backup ou
disco comprometido sem criptografia exporiria esse contexto.

**A solução.**

- `app/admin/crypto.py::encrypt_evidence` **redige a PII reconhecível** (e-mail, CPF, número de IDoc; `app/redaction.py::redact_pii_deep`) e cifra com Fernet (`LLM_CREDENTIALS_MASTER_KEY`). Quem decifra (a API admin, para a tela de incidentes) não recebe o dado pessoal.
- Sem master key, `encrypt_evidence` levanta `ConfigurationError` em vez de devolver `None`. A versão anterior perdia a evidência em silêncio e logava "deixando em claro". O boot falha quando `DATABASE_URL` está configurada sem a chave (`app/main.py::_ensure_evidence_key_configured`).
- `decrypt_evidence` aceita linhas legadas (lista/dict do JSONB, ou texto JSON) e levanta `ConfigurationError` quando o token não decifra com a chave atual.
- `app/services/incident_recorder.py::build_incident_row` e `app/services/incident_repository.py` usam a mesma função (havia três cópias).
- Migration `009_encrypt_evidence_json.py`:
  - **upgrade** cifra só as linhas em claro, com a mesma redação do runtime;
  - banco novo, ou já migrado, sobe **sem exigir a chave** (o job de migrações do CI não tem chave);
  - com linhas pendentes e sem chave, falha com uma mensagem que diz quantas linhas estão pendentes.
  - **Downgrade** decifra e só exige a chave quando há linhas cifradas.
  - Exercitado contra PostgreSQL 16 real na validação de 2026-10-06.

**Limitações (aceitas):**

- A redação é por regex: dado empresarial fora dos padrões (nomes, números de contrato) continua dentro do cifrado.
- A detecção de "já cifrado" na migration usa o prefixo `gAAA` dos tokens Fernet.
- Downgrade em produção devolve a evidência em claro. Use só para rollback imediato.


## Invariantes que NÃO devem ser alterados sem DA formal

1. `RERANKER_MODEL` → sempre `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (não o baseline ms-marco-L6)
2. `rule_engine_enabled` → `True` por default; desligar SÓ em testes que precisam forçar LLM
3. AI Gateway (`invoke_via_gateway`) é o ÚNICO ponto de entrada para o LLM no grafo — não chamar `factory.invoke_with_hybrid_fallback()` diretamente
4. `_assemble_evidence()` NUNCA usa autoavaliação do LLM — só fontes observáveis deterministicamente
5. Capability Registry (`mcp/policy.py`) é FAIL-CLOSED — tool sem entrada no registry é negada
6. `classify_domain()` em `supervisor.py` é 100% determinístico (sem LLM)
7. `generic_diagnosis_node` existe para `agent_domain="generic"` — não reutilizar `saas_diagnosis_node`
8. **Modelo NUNCA entra na tabela de rotas** — `llm_model` é texto livre; `llm/routes.py` declara só ONDE o dado sai e O QUE o destino aceita (DA-45). Há teste que falha se um nome de modelo entrar como chave de rota
9. **Capacidade é por ORIGIN, não por rótulo** — `openai` apontando para Gemini e para api.openai.com não são o mesmo destino. Mesma razão de DA-43 (DA-45)
10. `llm_send_seed=None` (default) = DA-2 ativa (seed=42). `None` NÃO pode ser lido como "não enviar" — destino desconhecido recebe seed; só origens registradas como incompatíveis não recebem
11. Rota `require_loopback` e `require_loopback=False` são mutuamente exclusivos — `local_lab` apontando para a internet, e `enterprise_azure` apontando para loopback, falham no boot
12. **Correlação incidente↔sistema é fail-closed** — `app/admin/correlation.py` só resolve por `connector_type` quando há UM único candidato; com 2+ devolve `ambiguous` com a lista. Nenhuma superfície (UI, API, dashboard) escolhe um sistema por conta própria (DA-50)
13. `verified` ≠ `verified_at` — `POST /incidents/{id}/verify` grava `verified_at` sempre, mas `diagnosis_correct=None` fica NULL. Coagir para `True` infla a acurácia nos dashboards (DA-50)
14. **Gate de qualidade roda junto com a suite** — `uv run python scripts/quality_gate.py` (DA-51) valida dataset de avaliação, corpus, invariante do reranker, configs do promptfoo, a lista de DAs candidatas **e o livro-razão das DAs em três direções**: `implemented_das_documented` (prosa ↔ registro), `das_index_current` (índice do README) e `da_registered` (DA citada em `app/`, `scripts/`, `alembic/` tem linha na tabela). A terceira direção só existe porque nove DAs estavam fora do registro com a prosa apenas na docstring — `uv run python scripts/quality_gate.py` (DA-51) valida dataset de avaliação, corpus, invariante do reranker, configs do promptfoo, a lista de DAs candidatas, **a documentação das DAs** (inclusive `da_registered`: DA citada no código sem linha na tabela reprova) (`implemented_das_documented` + `das_index_current`: DA registrada sem prosa localizável reprova, assim como seção órfã). Prosa de decisão mora no `README.md` (`### N. Título (DA-N)`); `docs/ARCHITECTURE.md` é local alternativo declarado para DA-32/33/34/35. DAs entregues juntas compartilham uma seção (`(DA-46/47/48)`). `docs/QUALITY_GATES.md` documenta o que eles NÃO cobrem
15. **Ausência de evidência nunca é "sem drift"** — a DA-52 tem **quatro** estados (`clean`, `drift`, `first_observation`, `unverified`) e `unverified` é um deles. `first_observation` (sem baseline) e `unverified` (sem leitura) são distintos de `clean`, e `unverified` nunca abre incidente nem grava/apaga baseline
16. **`system_contracts` é append-only e sem FK** para `integration_systems` — histórico de observação, não cadastro. Migration 005; desde a 011 um trigger nega UPDATE/DELETE (TRUNCATE segue permitido)
17. **Só `breaking` abre incidente** de drift; additive não. Rename provável é **breaking com `hint`** (`app/contracts/diff.py::_pair_renames`): para o consumidor o efeito é o mesmo de remover o campo, e o hint evita dois alarmes. `cosmetic` não é emitido hoje (a normalização descarta anotação/versão/namespace, invariante 18). Breaking cuja entrega falha **não grava baseline**: a próxima observação re-detecta e reemite (validação 2026-10-07)
18. **Fingerprint nunca é do XML bruto** — Properties/Entities/Annotations são `tuple` ordenadas e namespace/versão volátil ficam de fora. Sem isso o SAP republicando o serviço gera drift todo dia
19. `get_sync_session_factory()` é cacheado **chaveado pela URL** e nunca cacheia `None` — `lru_cache` de zero args sobre `settings` mutável travava `None` em cache sem erro (achado pelo e2e da DA-52)
20. **Texto de prompt só muda com o promptfoo junto** — o gate `prompt_digest_measured` (DA-53) reprova se o digest de `app/agent/prompts.py` divergir de `data/eval/prompt_baseline.json`. Isso inclui editar um `Field(description=)` do `DiagnosisModel`, que o LangChain injeta no schema de tool-calling. Depois de mudar de propósito: rode o promptfoo e regrave com `--write-prompt-baseline`
21. **`prompt_digest`/`prompt_version` são NULL quando o rule engine encerra** (DA-53) — um diagnóstico sem LLM não foi produzido por prompt nenhum. Default `"desconhecido"` fabricaria procedência, o mesmo erro da invariante 13
22. `evidence_strength` é FLOAT (migration 002) — nunca usar predicado textual (`IN ('high','critical')`) em query de dashboard/relatório. `scripts/validate_dashboards.py` roda as 45 queries contra o Postgres real antes de dar o dashboard como bom
23. **Nenhum conector novo entra só no registro** — `po` (DA-56) precisou de 7 edições em 6 arquivos: registro (`_REGISTRY` + `_REAL_MODE_SETTING`), os **dois** Literals de `interface_type` em `app/models.py` (request e envelope), supervisor, choices do CLI, dropdown da UI e `CONNECTOR_TYPES` do catálogo admin. O gate `connector_reachable` cobre as seis superfícies, e cada Literal é conferido por separado — a união dos dois mascararia a queda de um deles. Sem a 6ª superfície (`CONNECTOR_TYPES`), o conector aceito em todo o produto fica invisível na tela que responde "qual sistema é", que é onde a correlação DA-50 acontece. A DA-57 adicionou a **7ª superfície** (seed de `web_search_sources` na migration 008) e a **8ª** (`<select name="connector_type">` de `app/admin/templates/systems.html`). As duas ausências são de tipos diferentes e por isso as duas são verificadas: sem linha no seed o conector fica **sem busca web, sem erro nenhum**; sem linha no formulário ele **não tem como ter `integration_system`**, e a correlação DA-50 cai no fallback por `connector_type`, que é fail-closed com ambiguidade. A segunda não é morte do conector, é falha silenciosa de correlação — e por isso ela não pode ser inferida de `CONNECTOR_TYPES`, que é a-tupla do catálogo, não o formulário. A DA-58 adicionou a **9ª superfície** (`data/connector_coverage.yaml`), verificada por um gate **próprio** (`connector_coverage`) e não dentro de `connector_reachable`: conector registrado sem linha de cobertura funciona perfeitamente e simplesmente **não aparece no mapa**, o que subestima o alcance do repo sem erro nenhum. Ficou em gate separado porque a falha ali tem nome e mensagem próprios, e a resposta genérica de `connector_reachable` ("não aparece nessa superfície") esconderia qual dos dois eixos quebrou
24. **`web_search_sources` é fail-closed e não tem fallback em código** (DA-57) — `resolve_approved_source()` devolve `None` (⇒ sem busca web) sem `DATABASE_URL`, sem tabela, sem linha, com linha desabilitada ou sem `site_filter`/`tech_term`; **falha de banco também é `None`, não exceção**, porque é um fallback opcional do RAG. Nunca reintroduzir um dict de emergência: ele traria de volta o `approved` que era no-op, e o efeito seria invisível. `interface_type` é imutável no PATCH (trocá-lo deixaria a linha servindo o conector errado)
25. **Cobertura não é booleano e `unknown` não é `none`** (DA-58) — o mapa tem 3 níveis: `dedicated` (conector feito para o produto), `generic` (cliente de **mecanismo** alcançaria se a URL apontasse, **nunca validado** contra ele) e `absent`. Colapsar em booleano faria "cliente OData alcançaria" virar "temos conector de S/4HANA", que é a mentira que a matriz de `docs/ARCHITECTURE.md` já proíbe com outro vocabulário. Mecanismo `unknown` **não** vira lacuna: a lacuna vira trabalho de código, e a ausência de dado é trabalho de fonte. `MDI` é nome de mecanismo (`{level, note}`), nunca nível. E a **armadilha registrada**: o docstring de `apim_connector.py` diz "API Management / Integration Suite", mas o código lê eventos de *analytics* — é observabilidade, não orquestração, então **não** fecha a coluna `integration_suite` (22 lacunas). Como as 76 lacunas são o relatório e não um defeito, o gate `connector_coverage` reprova só por **incoerência** (conector sem linha, produto fantasma, doc desatualizado)

---

## Trust levels da Evidence Layer

| trust_level | Quando |
|---|---|
| `system_observed` | Conector real (não mock) OU rule engine (DA-33) |
| `simulated` | Conector mock ou fallback |
| `retrieved_document` | Chunk RAG (Qdrant) ou histórico GraphRAG |
| `web_untrusted` | Resultado de busca web (DuckDuckGo) |
| `user_reported` | Descrição textual do incidente (mais fraco) |

---

## Limitações conhecidas (aceitas, não regredir)

- Testes de integração (`-m integration`) requerem Qdrant/Ollama locais; são pulados automaticamente sem eles
- `StreamableHTTPSessionManager.run()` — só pode ser chamado UMA vez por processo
- GraphRAG (Neo4j) é opt-in; desligado por default — não ativar em testes unitários
- Backend `device_bash` cloud não alcança `localhost` da máquina do usuário — usar Claude Code CLI local para testes de integração reais
- `starlette.Mount()` não propaga lifespan ASGI para sub-apps
- **Matriz de validação dos conectores é uma afirmação por conector** — RFC, ServiceNow, Salesforce e CAP foram validados contra instância real; **OData, Workday, Ariba, SuccessFactors e PO/PI não** (PO/PI por causa de API não pública); APIManagement tem schema **especulativo**, nem formato confirmado. `docs/ARCHITECTURE.md` é a fonte, e o gate `connector_validation_matrix` exige que ela cubra todo conector registrado — mas o gate atesta que a afirmação *existe*, não que seja verdadeira. Nunca apresentar "tem conector" como "foi validado"

---

## Como registrar uma nova DA

1. Implementar e validar com testes
2. Adicionar entrada na tabela de DAs acima neste `CLAUDE.md`
3. Adicionar seção `### N. Título (DA-N)` no `README.md` com problema/solução/limitações
   — o `(DA-N)` é obrigatório: sem ele a prosa existe mas é invisível para qualquer
   busca por número, que foi exatamente o que aconteceu com 15 seções. DAs entregues
   na mesma mudança podem compartilhar a seção (`(DA-46/47/48)`)
4. Acrescentar a linha `| DA-N | ... |` na tabela acima e a entrada no **índice** do `README.md`
   (seção `## Decisões de Arquitetura`). Os dois gates reprovam o build se algum dos dois faltar
5. Commitar com prefixo `feat(DA-N):` no commit message

---

## Debug rápido no VS Code

`.vscode/launch.json` tem 13 configurações prontas:
- **Debug: graph.py (caso IDoc travado)** — exercita RFC + regra 51
- **Debug: FastAPI (uvicorn)** — servidor com breakpoints
- **Debug: pytest (tudo)** / **(só unitários, rápido)**
- **Debug: graph.py (texto livre)** — prompt interativo

Ver também: `docs/TUTORIAL_ARQUITETURA_DEBUG.md`
