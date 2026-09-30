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
| Modelo canônico | `qwen3-coder-next:latest` (MoE 80B/3B ativo, 262K ctx; 10/10 no promptfoo na Fase 12 — substituiu `qwen2.5-coder:32b`, ver DA-4/8 + Fase 12) |
| RAG | LangChain + Qdrant (hybrid dense+sparse BM25, fusão RRF) |
| Reranker | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (benchmark DA-29; não usar ms-marco-L6) |
| GraphRAG | Neo4j (`app/rag/graph_store.py`), opt-in via `USE_GRAPH_RAG=true` |
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

**Infraestrutura local** (`~/ai-stack` via docker compose):
- Qdrant → `localhost:6333`
- Neo4j → `localhost:7474`
- Langfuse → `localhost:3000`
- Ollama → `localhost:11434`

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
    rules.py        # DA-33: Rule Engine determinístico (21 regras SAP/integração)
    escalation.py   # DA-44: sinal determinístico de escalonamento (3 tiers)
    supervisor.py   # DA-22: classifica domínio (sap/saas/generic) sem LLM
    state.py        # CopilotState (TypedDict)
  connectors/       # 9 conectores: odata, rfc, servicenow, salesforce,
                    # workday, ariba, successfactors, cap, apimanagement
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
  events/
    consumer.py     # DA-23: webhook CloudEvents → run_diagnosis()
  a2a/              # DA-14: Agent2Agent (JSON-RPC 2.0)
  auth.py           # DA-54: sessão da UI (login/cookie HMAC + logout/
                    # session) e DA-55: rotas públicas de ativação
                    # (/auth/verify/email, /auth/verify/phone); o login
                    # verifica .env (bootstrap) → banco (web_users ativos)
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
                    # 10 deles são e2e da DA-52 e só rodam com
                    # IIC_TEST_DATABASE_URL + Postgres (job CI
                    # migrations_and_dashboards)
data/
  sample_docs/      # Base de conhecimento RAG (arquivos .md)
  eval/             # Dataset de avaliação RAG + resultados benchmark
                    # + baseline do promptfoo (DA-51), versionado quando existir
scripts/            # benchmark_rerankers.py, generate_reports.py,
                    # validate_dashboards.py (45 queries Grafana vs Postgres real),
                    # quality_gate.py (DA-51)
docs/               # índice em README.md; ARCHITECTURE.md, GETTING_STARTED.md,
                    # TUTORIAL_ARQUITETURA_DEBUG.md, TROUBLESHOOTING.md,
                    # QUALITY_GATES.md, e o resto (17 .md no total)
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
| DA-33 | Rule Engine determinístico (pré-filtro LLM, 21 regras SAP) | `agent/rules.py` |
| DA-43 | Soberania de dados por origin real, fail-closed | `llm/gateway.py` + `GET /llm/policy` |
| DA-44 | Sinal determinístico de escalonamento em 3 tiers (prep. tier 3) | `agent/escalation.py` |
| DA-45 | Universalidade de provider: rota auditada + capacidades por origin + identidade de embedding | `llm/routes.py`, `llm/capabilities.py`, `llm/origins.py`, `rag/embedding_guard.py` |
| DA-46 | Registro gerenciado de modelos/credenciais por ORIGEM (LLM_REGISTRY_DB, fail-closed) | `app/admin/` (models, repository, runtime, routes, ui) |
| DA-47 | Credenciais cifradas em repouso com Fernet (master key no .env, nunca em runtime) | `app/admin/crypto.py` |
| DA-48 | Metering de tokens REAIS (usage_metadata, não estimativa) persistido best-effort | `app/llm/gateway.py` + `app/admin/metering.py` |
| DA-49 | Catálogo de sistemas integrados (`integration_systems`) na superfície admin, `connector_type` = Literal do pipeline | `app/admin/` (models, repository, routes, ui) + `alembic/004` |
| DA-50 | Correlação `incidents` ↔ catálogo por `system_key` (exato, vindo de `IncidentRequest.connector_source_system`) com fallback por `connector_type` e ambiguidade fail-closed; verificação persistida no SQL; tela `/admin/incidents` + dashboard `iic-systems` | `app/admin/correlation.py`, `app/agent/graph.py`, `app/services/incident_recorder.py::record_verification`, `app/admin/routes.py`, `scripts/validate_dashboards.py` |
| DA-51 | Quality gates: invariantes de avaliação verificadas por máquina (dataset/corpus, invariante DA-29, configs promptfoo, freshness das DAs candidatas) + **integridade da documentação** (fences, links, referências `app/x.py::símbolo`) + **alcançabilidade de conectores** (registro → Literal do pipeline → supervisor) + migrações e 45 queries no CI | `app/evaluation/gates.py`, `scripts/quality_gate.py`, `.github/workflows/quality.yml`, `docs/QUALITY_GATES.md` |
| DA-52 | Detecção de drift de contrato SAP: probe `$metadata` (interface segregada `fetch_contract`), normalização+hash canônico, severidade fechada (breaking/additive/cosmetic), baseline append-only `system_contracts` (migration 005) e sinal via event mesh só em breaking | `app/contracts/` (`model`, `odata`, `diff`, `baseline`, `observe`), `app/connectors/odata_connector.py::fetch_contract`, `scripts/check_contract_drift.py` |
| DA-53 | Prompt de diagnóstico como artefato versionado: `PromptSpec` (version+digest) em módulo próprio, proveniência (`llm_model`/`prompt_version`/`prompt_digest`) na resposta, no relatório e em `incidents` (migration 006), e gate `prompt_digest_measured` amarra produção ao prompt medido | `app/agent/prompts.py`, `app/evaluation/gates.py::check_prompt_digest`, `data/eval/prompt_baseline.json` |
| DA-54 | Login de sessão para a UI web: `POST /auth/login` (usuário+senha → cookie HttpOnly assinado HMAC) como alternativa à `X-API-Key` em `/diagnose`; superfícies de máquina (MCP/A2A/events/admin) seguem só com chaves dedicadas | `app/auth.py`, `app/main.py`, `frontend/src/api/auth.ts` |
| DA-55 | Manutenção de usuários da UI pelo admin: tabela `web_users` (migration 007), ativação em duas etapas (token e-mail → código telefone, out-of-band até haver SMTP/SMS), CRUD `/admin/api/users` + tela `/admin/users`; login verifica banco **e** `.env` (bootstrap nunca desliga) | `app/webusers.py`, `app/admin/` (models, routes, ui), `app/auth.py` (`/auth/verify/*`, login env→banco), migration 007 |

**DAs candidatas (sem implementação ainda):**
- DA-31: SAP AI Agent Hub registration (MCP + A2A) — bloqueada: exige tenant Kyma

> A DA-32 (AMQP 1.0 via Solace Cloud) **já foi entregue** (`app/events/amqp_consumer.py`,
> commit `67b78e8`). Ela ficou nesta lista até a DA-51 criar o gate
> `candidate_das_fresh`, que falha o build quando uma DA marcada como candidata
> já tem seção em `docs/ARCHITECTURE.md`.

---

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
14. **Gate de qualidade roda junto com a suite** — `uv run python scripts/quality_gate.py` (DA-51) valida dataset de avaliação, corpus, invariante do reranker, configs do promptfoo, a lista de DAs candidatas **e a documentação das DAs** (`implemented_das_documented` + `das_index_current`: DA registrada sem prosa localizável reprova, assim como seção órfã). Prosa de decisão mora no `README.md` (`### N. Título (DA-N)`); `docs/ARCHITECTURE.md` é local alternativo declarado para DA-32/33/34/35. DAs entregues juntas compartilham uma seção (`(DA-46/47/48)`). `docs/QUALITY_GATES.md` documenta o que eles NÃO cobrem
15. **Ausência de evidência nunca é "sem drift"** — a DA-52 tem **quatro** estados (`clean`, `drift`, `first_observation`, `unverified`) e `unverified` é um deles. `first_observation` (sem baseline) e `unverified` (sem leitura) são distintos de `clean`, e `unverified` nunca abre incidente nem grava/apaga baseline
16. **`system_contracts` é append-only e sem FK** para `integration_systems` — histórico de observação, não cadastro. Migration 005
17. **So `breaking` abre incidente** de drift; additive e cosmetic não. Rename provável é *cosmetic*: errar para breaking gera alarme falso e o detector é desligado
18. **Fingerprint nunca é do XML bruto** — Properties/Entities/Annotations são `tuple` ordenadas e namespace/versão volátil ficam de fora. Sem isso o SAP republicando o serviço gera drift todo dia
19. `get_sync_session_factory()` é cacheado **chaveado pela URL** e nunca cacheia `None` — `lru_cache` de zero args sobre `settings` mutável travava `None` em cache sem erro (achado pelo e2e da DA-52)
20. **Texto de prompt só muda com o promptfoo junto** — o gate `prompt_digest_measured` (DA-53) reprova se o digest de `app/agent/prompts.py` divergir de `data/eval/prompt_baseline.json`. Isso inclui editar um `Field(description=)` do `DiagnosisModel`, que o LangChain injeta no schema de tool-calling. Depois de mudar de propósito: rode o promptfoo e regrave com `--write-prompt-baseline`
21. **`prompt_digest`/`prompt_version` são NULL quando o rule engine encerra** (DA-53) — um diagnóstico sem LLM não foi produzido por prompt nenhum. Default `"desconhecido"` fabricaria procedência, o mesmo erro da invariante 13
22. `evidence_strength` é FLOAT (migration 002) — nunca usar predicado textual (`IN ('high','critical')`) em query de dashboard/relatório. `scripts/validate_dashboards.py` roda as 45 queries contra o Postgres real antes de dar o dashboard como bom

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
