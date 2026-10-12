# BASELINE_REPORT.md — Integration Incident Copilot

**Data:** 2026-10-12
**Versão do documento:** 1.0
**Ambiente:** Linux (ROCm), Python 3.14.4, uv 0.12.17

---

## 1. Estado inicial do Git

- **Branch:** `master`
- **Commit atual:** `370ab6f` (docs(DA-8): revisao formal — falha JSON era bug de runtime Ollama #10929)
- **Status:** 3 arquivos modificados (CLAUDE.md, docs/AVALIACAO_MODELOS_LOCAIS.md, docs/DECISOES_DE_ARQUITETURA.md), 16 arquivos não rastreados

---

## 2. Sistema operacional e versões

| Componente | Versão |
|------------|--------|
| SO | Linux (ROCm) |
| Python | 3.14.4 |
| uv | 0.12.17 |
| pip | 25.1.1 |
| Docker | 29.1.3 |
| Docker Compose | 2.40.3+ds1-0ubuntu1 |

---

## 3. Dependências e grupos de instalação

### Dependências principais (pyproject.toml)

- fastapi>=0.115
- uvicorn[standard]>=0.32
- pydantic>=2.9
- langgraph>=1.2
- langchain>=1.3
- langchain-ollama>=1.1
- qdrant-client>=1.19
- neo4j>=6.2
- langfuse>=4.14
- pypdf>=6.16.1
- fastembed>=0.8.0
- sentence-transformers>=6.0.1
- torch>=2.14.1 (so-CPU no Linux via index pytorch-cpu)
- slowapi>=0.1.10
- mcp>=1.9.0
- redis>=5.2
- sqlalchemy[asyncio]>=2.0
- alembic>=1.13
- asyncpg>=0.29
- cryptography>=42
- jinja2>=3.1
- python-qpid-proton>=0.39
- rq>=2.12.0
- rdflib>=7.6.0

### Grupos de desenvolvimento (dev)

- debugpy>=1.8.21
- httpx>=0.28.1
- huggingface-hub>=0.30
- langchain-openai>=1.6.0
- pre-commit>=4.6.1
- psutil>=7.2
- pytest>=9.1.1
- pytest-asyncio>=1.4.0
- aiosqlite>=0.20
- ruff>=0.16.1
- pytest-cov>=6.0
- bandit>=1.8
- pip-audit>=2.7

### Extras

- `openai`: langchain-openai>=0.3 (opcional, apenas se LLM_PROVIDER=openai/azure_openai)
- `reports`: openpyxl, jinja2, psycopg2-binary
- `prometheus`: prometheus-fastapi-instrumentator>=7.0

---

## 4. Serviços externos necessários

| Serviço | Status | Observações |
|---------|--------|-------------|
| Ollama | Local (host) | API em `http://127.0.0.1:11434` |
| Qdrant | Docker Compose | Vector store para RAG |
| Neo4j | Docker Compose | GraphRAG (opcional) |
| Redis | Docker Compose | Fila RQ (opcional) |
| PostgreSQL | Docker Compose | Persistência de incidentes |
| Grafana | Docker Compose | Observabilidade |

---

## 5. Configuração de testes e quality gates

### Testes

- **Comando:** `uv run pytest tests/ -m "not integration" -v --tb=short`
- **Resultados:** 1495 passed, 15 skipped, 3 warnings
- **Markers:** `browser`, `integration`, `neo4j_smoke`, `rag_quality`

### Quality gates

- **Comando:** `uv run python scripts/quality_gate.py`
- **Resultados:** Todos os checks PASS (18/18), 3 WARNs (informativos)

| Check | Status |
|-------|--------|
| connector_validation_matrix | PASS |
| connector_coverage | PASS |
| rag_dataset_schema | PASS |
| corpus_coverage | PASS |
| dataset_difficulty_mix | PASS |
| reranker_invariant | WARN (não significativo) |
| promptfoo_configs | PASS |
| llm_baseline | PASS |
| deployed_model_evaluated | WARN (gpt-4o-mini vs qwen3.8:27b) |
| index_manifest | WARN (sem sap_reference_library) |
| candidate_das_fresh | PASS |
| implemented_das_documented | PASS |
| da_registered | PASS |
| das_index_current | PASS |
| preflight_delegates | PASS |
| prompt_digest_measured | PASS |
| docs_markup_integrity | PASS |
| docs_code_references | PASS |
| docs_env_vars | PASS |
| connector_reachable | PASS |

---

## 6. Comandos executados

| Comando | Código de saída | Resultado |
|---------|-----------------|-----------|
| `uv sync --frozen` | 0 | Sucesso |
| `uv run pytest tests/ -m "not integration"` | 0 | 1495 passed |
| `uv run python scripts/quality_gate.py` | 0 | Tudo passou |

---

## 7. Limitações conhecidas

1. **Reports directory:** Inicialmente com permissões root, remediado com `rm -rf` + `mkdir -p`
2. **Reranker invariant:** Não há melhoria estatisticamente significativa sobre baseline (n=18, p=1.0)
3. **Deployed model:** Produção usa `gpt-4o-mini`, baseline mediu `qwen3.8:27b`
4. **Index manifest:** Falta `sap_reference_library` para fallback sem índice

---

## 8. Próximos passos

- Executar testes de integração (requer Ollama + Qdrant vivos)
- Executar quality gate completo
- Iniciar Fase 1: Harness de avaliação e Promptfoo

---

**Fim do baseline.**
