# Changelog

Todas as mudanças relevantes do projeto são documentadas aqui.
Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/).

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
