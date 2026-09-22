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
| Modelo canônico | `qwen2.5-coder:32b` (avaliado via promptfoo; `qwen3-coder-next` descartado por OOM) |
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
    nodes.py        # Todos os nodes: connector, retrieve, diagnose, report
                    # + _assemble_evidence(), _apply_confidence_guardrails()
    rules.py        # DA-33: Rule Engine determinístico (14 regras SAP/integração)
    supervisor.py   # DA-22: classifica domínio (sap/saas/generic) sem LLM
    state.py        # CopilotState (TypedDict)
  connectors/       # 8 conectores: odata, rfc, servicenow, salesforce,
                    # workday, ariba, cap, apimanagement
  llm/
    factory.py      # DA-20: Hybrid Inference (Ollama → cloud fallback)
    gateway.py      # DA-26: AI Gateway (policy + circuit breaker + budget)
  mcp/
    server.py       # DA-19: servidor MCP
    policy.py       # DA-27: Capability Registry (FAIL-CLOSED por default)
  rag/
    retriever.py    # RAG híbrido + reranker
    graph_store.py  # GraphRAG (Neo4j)
    ingest.py       # Indexação de documentos
  events/
    consumer.py     # DA-23: webhook CloudEvents → run_diagnosis()
  a2a/              # DA-14: Agent2Agent (JSON-RPC 2.0)
  config.py         # Pydantic Settings — fonte única de verdade para config
  main.py           # FastAPI app, rotas, lifespan

tests/              # 384 testes (unitários + integração)
data/
  sample_docs/      # Base de conhecimento RAG (arquivos .md)
  eval/             # Dataset de avaliação RAG + resultados benchmark
scripts/            # benchmark_rerankers.py, etc.
docs/               # ARCHITECTURE.md, GETTING_STARTED.md, TUTORIAL_ARQUITETURA_DEBUG.md
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
| DA-4/8 | Modelo escolhido via promptfoo: `qwen2.5-coder:32b` | `config.py::llm_model` |
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
| DA-33 | Rule Engine determinístico (pré-filtro LLM, 14 regras SAP) | `agent/rules.py` |

**DAs candidatas (sem implementação ainda — aguardam Kyma):**
- DA-31: SAP AI Agent Hub registration (MCP + A2A)
- DA-32: AMQP async consumer (Event Mesh fila real)

---

## Invariantes que NÃO devem ser alterados sem DA formal

1. `RERANKER_MODEL` → sempre `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (não o baseline ms-marco-L6)
2. `rule_engine_enabled` → `True` por default; desligar SÓ em testes que precisam forçar LLM
3. AI Gateway (`invoke_via_gateway`) é o ÚNICO ponto de entrada para o LLM no grafo — não chamar `factory.invoke_with_hybrid_fallback()` diretamente
4. `_assemble_evidence()` NUNCA usa autoavaliação do LLM — só fontes observáveis deterministicamente
5. Capability Registry (`mcp/policy.py`) é FAIL-CLOSED — tool sem entrada no registry é negada
6. `classify_domain()` em `supervisor.py` é 100% determinístico (sem LLM)
7. `generic_diagnosis_node` existe para `agent_domain="generic"` — não reutilizar `saas_diagnosis_node`

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
- `StreamableHTTPSessionManager.run()` — só pode ser chamado UMA vez por processo (ver `learnings.md`)
- GraphRAG (Neo4j) é opt-in; desligado por default — não ativar em testes unitários
- Backend `device_bash` cloud não alcança `localhost` da máquina do usuário — usar Claude Code CLI local para testes de integração reais
- `starlette.Mount()` não propaga lifespan ASGI para sub-apps (ver `learnings.md` sobre MCP)

---

## Como registrar uma nova DA

1. Implementar e validar com testes
2. Adicionar entrada na tabela de DAs acima neste `CLAUDE.md`
3. Adicionar seção `### N. Título (DA-N)` no `README.md` com problema/solução/limitações
4. Commitar com prefixo `feat(DA-N):` no commit message

---

## Debug rápido no VS Code

`.vscode/launch.json` tem 13 configurações prontas:
- **Debug: graph.py (caso IDoc travado)** — exercita RFC + regra 51
- **Debug: FastAPI (uvicorn)** — servidor com breakpoints
- **Debug: pytest (tudo)** / **(só unitários, rápido)**
- **Debug: graph.py (texto livre)** — prompt interativo

Ver também: `docs/TUTORIAL_ARQUITETURA_DEBUG.md`
