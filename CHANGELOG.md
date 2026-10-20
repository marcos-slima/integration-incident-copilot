# Changelog

Todas as mudanças relevantes do projeto são documentadas aqui.
Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/).

---

## [Não lançado] — DAs 75–92 e auditoria 2026-10-20 (branch `docs/en-translation`)

Lote entregue entre 19 e 20/10, mais a auditoria técnica de 2026-10-20
(25 achados, `IIC-AUD-001`…`025`). O status abaixo é o real — o que está
entregue, o que é schema-only e o que voltou para candidata.

| DA | Status | Evidência / observação |
|---|---|---|
| DA-75 | Entregue | `interrupt()` nativo do LangGraph + `Command(resume=...)`; `/incidents/{id}/review` usa checkpoint |
| DA-76 | Entregue | `ai_warning` em `DiagnosisResponse`, catálogo pt-BR/en e relatório |
| DA-79 | Candidata | Schema (`tenant_id`) e filtros de repositório existem; isolamento não é fail-closed em todos os caminhos |
| DA-82 | Schema-only | Tabelas `snapshot_table`/`rollback_log`; endpoint removido até existir mecanismo real |
| DA-83 | Entregue | `explanation` em `DiagnosisResponse` e `_build_xai_explanation` |
| DA-84 | Candidata | Tabela, ORM e repositório; endpoints de versão/rollback ausentes |
| DA-85 | Candidata | Tabela, ORM e repositório; endpoints e `_run_ab_test()` ausentes |
| DA-86 | Entregue | Endpoints de versões/histórico/rollback; UI de histórico ainda pendente |
| DA-87 | Entregue | `/admin/metrics/domains` com janela `days`; rota duplicada `/admin/dashboard/domains` removida |
| DA-88 | Entregue | `app/scripts/load_test.py`, config e `tests/test_load_testing.py` (3 testes) |
| DA-89 | Entregue | Export PDF/XLSX de compliance |
| DA-90 | Entregue | Documentação GRC e testes de conformidade |
| DA-91 | Entregue | Testes RBAC com status exatos; rotas DA-87/89 exercitadas |
| DA-92 | Registrada | Modelo canônico `qwen3.6:35b-a3b` desde 2026-10-13; re-baseline do promptfoo pendente (DA-51) |

### Correções da auditoria de 2026-10-20

- **001/015** HITL nativo: escalonamento antes do LLM, `hitl_request` no topo do
  estado, retomada por `Command(resume=...)`, sem conexão Postgres própria.
- **002/005/021** Admin/RBAC: `/admin/deploy/rollback` removida; testes exigem
  `401`/`503` exatos; `/admin/dashboard/domains` removida e
  `/admin/metrics/domains` aceita `days`.
- **003/004/016** Livro-razão: DA-79/84/85 rebaixadas para candidatas e o
  processo de entrega em lote registrado como dívida (histórico preservado).
- **006/028** ORM × migrations: `Snapshot`/`RollbackLog`, índices e colunas de
  gateway alinhados; `alembic check` verde nas migrations 022–028.
- **007/013/017** CI: `nosec B704` justificado, `conftest` isola Redis/Neo4j,
  job instala `--extra prometheus`.
- **008** API: `ai_warning` e `explanation` chegam a `/diagnose`.
- **009/010/011/012/014/018/019/022/023** Documentação e higiene: tabela de DAs
  sem duplicatas, seções únicas, `--reset-collection` na troca de embedding,
  Grafana em 3002, Python fixado, `kyma-config` fora do índice, 14 configs de
  debug e WARNs conhecidos documentados.
- **020/024/025** Sem ação de código: bundle movido para fora do repositório;
  falha AMQP é do ambiente; nenhum segredo na árvore.

### Versionamento

A versão canônica continua **1.2.0** em `pyproject.toml`, `app/main.py` e
`app/a2a/agent_card.py` até a próxima release. As entradas `2.0.0`/`2.0.1`
abaixo são históricas de uma numeração experimental anterior e não devem ser
usadas para release; a próxima release será **1.3.0** (minor).

---

## [Não lançado] — validação 2026-10-06/07 (branch `fix/validacao-2026-10-06`)

Correções da auditoria de 2026-10-05, em blocos, cada um verificado em clone
limpo. O detalhe item a item está nos relatórios de cada bloco e nas
mensagens de commit.

### Documentação (Bloco 5)
- **Removidos** `docs/UC_01..09`, os dois documentos de auditoria e o
  `TESTING_E2E_USER_FLOW`: descreviam código inexistente e marcavam como
  corrigidos achados abertos.
- **Novo** `docs/CASOS_DE_USO.md`: nove cenários, funcional e técnico, com
  números medidos por `tests/test_casos_de_uso.py`.
- **Reescritos** `docs/CONNECTORS.md` (variáveis reais de cada conector) e
  `docs/TROUBLESHOOTING.md` (readiness em `/ready`, não `/health`).
- **Novo gate** `docs_env_vars`; `docs_code_references` passa a conferir
  intervalos `arquivo.py:10-20`.
- `REFERENCE_LIBRARY_FALLBACK_ENABLED` (M-24), `false` no deploy Kyma.

### Correções pós-DA-66/67/69/70 e posicionamento (2026-10-17/18, branch `docs/en-translation`)
Contagens de testes conferidas com `pytest --collect-only` em 2026-10-18.

- **DA-67** Orçamento por diagnóstico (`app/llm/budget.py`): reserva sobre o
  custo estimado, débito do custo real, `BudgetExhaustedError` →
  `FailureClass.BUDGET_EXHAUSTED`; `DIAGNOSIS_BUDGET_USD` e
  `LLM_BUDGET_RESERVE_RATIO`. Tokenizer com fallback `len//4` para qualquer
  falha de carga, sem retentativa. Testes: `test_budget_ledger.py` (15),
  `test_tokenizer_offline.py` (8).
- **DA-69** Checkpoint sem PII em claro (`app/agent/checkpointing.py`, redação
  no `put`/`put_writes`, inclusive valores inline no jsonb); `PostgresSaver`
  montado corretamente (`from_conn_string` era context manager; URL SQLAlchemy
  normalizada); `thread_id` passado por nome. Testes: `test_checkpointing.py`
  (15), `test_checkpointing_postgres.py` (1, marker `postgres_smoke`, validado
  contra Postgres real).
- **DA-66** `test_llm_routing.py` (18).
- **DA-71** Resultado da busca web sanitizado e delimitado antes de voltar ao
  LLM (`format_untrusted_web_hits`). Testes: `test_web_search_injection.py` (16).
- **DA-29** `reranker_invariant` por significância (McNemar pareado), não por
  vencedor pontual; benchmark refeito com n=90; mmarco mantido.
- **Avaliação** Dataset RAG de 28 para 110 casos (90 no escopo, 20 fora):
  Hit@1 95,6% (86/90), Hit@3 97,8%, MRR 0,967, 19/20 fora de escopo rejeitados.
- **DA-24** Bundle validado em Kubernetes real (k3d): overlay `deploy/local/`,
  `scripts/k3d-validate.sh`, `test_local_overlay.py` (10). Bugs corrigidos:
  `/app/data` sem escrita para `appuser` (`Dockerfile`), Secret regerado a cada
  execução, Pod não recriado com tag fixa. Evidência:
  `docs/EVIDENCIA_K3D_2026-10-18.md`; tutorial:
  `docs/TUTORIAL_K3D_KUBERNETES_LOCAL.md`.
- **Dependências** `tiktoken`, `langgraph-checkpoint-postgres`,
  `psycopg[binary]` declaradas; migrations 017/018 na convenção numérica.
- **UI** a camada de apresentação passa a mostrar o que o backend já devolvia.
  React: sinal de escalonamento (DA-44), evidências com `trust_level` e aviso
  para `simulated`/`web_untrusted` (DA-25/DA-71), proveniência (DA-53),
  soberania via `GET /llm/policy` (DA-43/DA-66) e mensagens por `code`, com
  `error_id`. Admin UI: proveniência, sinal e custo no detalhe do incidente;
  coluna "Sinal" na lista; evidência renderizada pelos campos reais (antes
  caía em JSON cru).
- **API** `BudgetExhaustedError` e `PolicyViolationError` deixam de virar 500
  "erro interno": 503 com `code` (`budget_exhausted`, `policy_violation`) e
  `error_id`; o motivo completo, que cita origens internas, fica só no log.
  Todos os erros de `/diagnose` ganham `code`. Testes:
  `test_gateway_refusal_http.py` (4).
- **DA-74** Conteúdo do diagnóstico no idioma pedido (`locale` em
  `/diagnose`, A2A, MCP e Event Mesh; a UI envia o idioma da tela): rule
  engine, avisos de confiança e relatório em pt-BR ou en; prompt `en` com
  digest próprio (pt-BR continua o medido; `en` aparece como não medido no
  gate); e-mail/SMS de ativação no idioma de quem cadastrou; motivos de
  soberania, de roteamento e notas dos conectores com código estável,
  traduzidos pelas UIs. Corrige: regras do TTL sem `probableRootCause`
  devolviam "Erro conhecido detectado via taxonomia SKOS." no lugar da causa
  curada de `rules.py`, e passos com vírgula eram quebrados em pedaços.
  Migration 021 (`incidents.locale`, `web_users.locale`); gate
  `content_i18n`. Testes: `test_i18n_content.py` (34).
- **DA-73** UIs no idioma do navegador: UI web (`react-i18next`) e Admin UI
  (Jinja + JS, catálogo embutido sem quebrar a CSP) em pt-BR e en, com seletor
  de idioma compartilhado (cookie `iic_locale`), datas/números/USD por `Intl`,
  erros com `code` estável traduzidos pela UI, pseudo-locale `?lng=xx` e gate
  `ui_i18n`. Corrige o `<html lang="en">` da UI web com conteúdo em português.
  Testes: `test_i18n.py` (23).
- **DA-72** Roteamento por registro de modelos: no modo gerenciado cada modelo
  ativo é candidato do Policy Engine, com parâmetros por modelo (transporte,
  janela de contexto, capacidades que só restringem a origem, domínios, nível,
  prioridade, limite mensal bloqueante), score custo × latência
  (`LLM_ROUTING_COST_WEIGHT`/`LLM_ROUTING_LATENCY_WEIGHT`), circuito por origem,
  failover pela lista e latência gravada por chamada. A decisão passa a respeitar
  os overrides da Admin UI (antes a lista de candidatos vinha sempre do `.env`).
  `/admin/models`: edição dos parâmetros, orçamento e pesos como overrides, e
  simulador (`GET /admin/api/routing/preview`). Provenance: `llm_model` registra o
  modelo que respondeu. Migration 020. Invariante 9 emendada. Testes:
  `test_registry_routing.py` (23), `test_registry_routing_repository.py` (5).
- **DA-67 (correção)** o roteamento buscava o preço registrado pelo rótulo do
  provider (`"openai"`) e nunca o encontrava: a estimativa caía na tabela
  interna (~33× o preço de `gpt-4o-mini`) e podia barrar o candidato por
  orçamento. Agora busca pelo modelo efetivo; no modo gerenciado, pelo default
  da origem. Testes: `test_llm_routing.py::TestCostEstimation` (+4).
- **Migration 019** `incidents.should_escalate`, `escalation_reason`,
  `cost_usd` e `llm_calls` (custo debitado do orçamento do diagnóstico).
  Testes: `test_incident_escalation_cost.py` (6).
- **Validação** suíte unitária na máquina de desenvolvimento: 1608 passando,
  cobertura 81,13% (antes das entregas de DA-71, avaliação e k3d); quality
  gate sem falha.

> As entradas abaixo são históricas: descrevem o projeto na data delas
> (ex.: a busca web hoje é configurada em `web_search_sources`, DA-57, e não
> tem fallback genérico).

---

## [1.2.0] — 2026-09-15

### Adicionado

**Web search direcionada por interface_type (v1.2)**
- Busca web agora usa fontes específicas por protocolo:
  - `rfc` → help.sap.com/docs/SAP_NETWEAVER + community.sap.com + github.com/SAP/PyRFC
  - `odata` → help.sap.com + community.sap.com/technology-blogs-by-sap
  - `cap` → cap.cloud.sap + github.com/SAP/cloud-cap-samples + community.sap.com
  - `servicenow` → developer.servicenow.com + community.sap.com
  - `salesforce` → developer.salesforce.com + community.sap.com
  - `ariba` → help.sap.com/docs/ARIBA + community.sap.com
  - `apim` → help.sap.com/docs/SAP_API_MANAGEMENT + community.sap.com
  - fallback genérico para casos sem interface_type
- Threshold configurável via `.env`: `WEB_SEARCH_THRESHOLD=0.6` (default)
- Web search desligável via `WEB_SEARCH_ENABLED=false`

---

## [1.1.0] — 2026-09-15

### Adicionado

**Web search fallback (v1.1)**
- Busca web via DuckDuckGo (`ddgs`) ativa automaticamente quando o melhor
  resultado RAG tem score < 0.6 — RAG local continua sendo o caminho primário
- Busca direcionada para SAP Community, GitHub SAP e SAP Help Portal
  (`site:community.sap.com OR site:github.com/SAP OR site:help.sap.com`)
- Retorna título, URL e resumo dos 5 melhores resultados, incorporados
  ao contexto do prompt como fonte secundária
- Privacidade: usa apenas a descrição textual do incidente, nunca dados
  do conector (que podem conter informações sensíveis do cliente)
- Novo node `web_search` no grafo LangGraph, entre `retrieve` e `diagnose`
- Compatível com GraphRAG opt-in — entra no pipeline antes do `graph_enrich`

---

## [2.0.1] — 2026-09-15

### Corrigido (double check P0/P1/P2)

- **P1 — Prompt injection** — sanitização em todos os campos não confiáveis
  (`description`, `logs`, `payload`, chunks do RAG) via `sanitize_untrusted_input()`
  em `app/agent/nodes.py`; neutraliza padrões conhecidos antes de entrar no prompt LLM
- **P2 — Imagens Docker fixadas** — `ollama/ollama:0.34.1`, `neo4j:5.26.0-community`
  (qdrant:v1.19.0 já estava fixado); reprodutibilidade garantida em todos os serviços
- **P2 — Ollama local** — atualizado para v0.34.1 (alinhado com docker-compose.yml)

---

## [2.0.0] — 2026-09-15

## [1.0.0] — 2026-09-15

Primeira versão estável. Pipeline completo de diagnóstico de incidentes de integração SAP e multi-vendor, com interface web, 8 conectores (4 validados contra sistema real), camada A2A, GraphRAG opt-in e documentação de produto completa.

### Adicionado

**Pipeline de diagnóstico (agente)**
- Orquestração via LangGraph (connector → retrieve → diagnose → report)
- GraphRAG opt-in via Neo4j (graph_enrich / graph_write, desligado por default)
- Guardrails determinísticos: teto de confiança 0.4 para fallback de conector, 0.3 para ausência de contexto RAG
- Validação estruturada de saída do LLM via with_structured_output(DiagnosisModel) com fallback tolerante
- Seed fixo (seed=42) para reproducibilidade com temperature=0

**Interface web**
- Frontend React servido pelo FastAPI em / (static/index.html)
- Formulário completo: descrição, sistema de origem, identificador, logs, payload (campos avançados)
- Resultado: causa raiz, badge de confiança colorido, próximos passos numerados, relatório Markdown expansível
- Histórico de diagnósticos da sessão e status da stack
- Funciona também como arquivo standalone no browser

**API e protocolos**
- REST: POST /diagnose, GET /health (FastAPI + Pydantic)
- A2A (Agent2Agent): Agent Card, JSON-RPC 2.0 (message/send, tasks/get) — protocolo aberto Linux Foundation
- Documentação interativa Swagger em /docs

**Conectores (8)**
- ODataConnector — OAuth2 Client Credentials + OData v2/v4
- RFCConnector — pyrfc 3.3.1 + SDK 7.50 PL19 · validado: ABAP Cloud Trial A4H rel 754
- ServiceNowConnector — Table API REST · validado: PDI real
- SalesforceConnector — OAuth2 + REST · validado: Developer Edition
- WorkdayConnector — OAuth2 + REST (sandbox gratuito indisponível)
- AribaConnector — OAuth2 + REST (sandbox incompatível)
- CAPConnector — OData v4 + XSUAA · validado: BTP Trial (HANA Cloud)
- APIManagementConnector — schema especulativo, não validado

**RAG e base de conhecimento**
- Busca híbrida dense + sparse BM25, fusão RRF via Qdrant
- Base de conhecimento local em data/sample_docs/ (10 documentos)
- Indexador incremental com suporte a .md e .pdf

**LLM Gateway plugável**
- qwen3-coder-next:latest como modelo de produção (MoE 80B/3B ativo, 262K ctx)
- Suporte a Ollama (default), OpenAI, Azure OpenAI e OpenRouter

**Qualidade e CI**
- 68 testes (54 unitários + 14 de integração)
- GitHub Actions: lint + testes a cada push/PR
- pre-commit: ruff, gitleaks, check-yaml, check-added-large-files
- Duas comparações formais de modelo via promptfoo (pipeline real)

**Documentação**
- docs/GETTING_STARTED.md — do zero ao primeiro diagnóstico em 10 minutos
- docs/USER_GUIDE.md — guia de produto com orientações de prompt, casos típicos, glossário SAP
- docs/ARCHITECTURE.md — detalhamento técnico por camada
- docs/PROCESSO_DESENVOLVIMENTO.md — 12 fases de desenvolvimento
- docs/TUTORIAL_ARQUITETURA_DEBUG.md — roteiro de debug no VS Code
- Diagramas Mermaid em todos os documentos principais

### Fora do escopo desta versão (roadmap)
- Validação real de Workday e Ariba
- APIManagementConnector com schema real
- Multi-tenancy CAP (MTX + Service Manager)
- Integração inbound A2A com Joule (aguardando GA Q4/2026)
- Autenticação JWT/OAuth2 na API
- Persistência de histórico entre sessões

---

O desenvolvimento foi feito em 12 fases documentadas em
docs/PROCESSO_DESENVOLVIMENTO.md.

---

*Integration Incident Copilot · github.com/marcos-slima/integration-incident-copilot*
