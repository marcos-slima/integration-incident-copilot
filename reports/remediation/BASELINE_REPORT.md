# BASELINE REPORT — Integration Incident Copilot

**Data:** 2026-10-12
**Commit:** `4c9a10ea52d34b763d52ca9159c9e2b02bac03fb`
**Branch:** `master`

## Ambiente

| Componente | Versão |
|------------|--------|
| Python | 3.14.4 |
| uv | 0.12.17 |
| Node.js | v24.20.0 |
| npm | 12.0.2 |
| Docker | 29.1.3 |
| Docker Compose | 2.40.3 |

## Estado do Git

- **Modificações locais:** 13 arquivos não rastreados (diagramas, resultados de avaliação, validador)
- **Arquivos modificados:** `docs/architecture.drawio`
- **Nenhum commit não enviado**

## Dependências

Todas as dependências do `pyproject.toml` foram resolvidas com `uv sync --frozen`:

- fastapi, uvicorn, pydantic, langgraph, langchain, langchain-ollama
- qdrant-client, neo4j, langfuse, python-dotenv
- fastembed, defusedxml, numpy, aiofiles, pymupdf4llm, sentence-transformers
- torch (so-CPU via índice pytorch-cpu)
- slowapi, mcp, redis, sqlalchemy, alembic, asyncpg, psycopg2-binary
- cryptography, jinja2, python-qpid-proton, rq, rdflib

Grupos extras:
- `dev`: debugpy, httpx, huggingface-hub, langchain-openai, pre-commit, psutil, pytest, pytest-asyncio, aiosqlite, ruff, pytest-cov, bandit, pip-audit

## Testes

### Unitários (não-integração)

```bash
uv run pytest -m "not integration" --tb=short -q
```

**Resultado:** `1495 passed, 15 skipped, 14 deselected, 5 warnings`

**Warnings:**
- `LangGraphDeprecatedSinceV10`: `create_react_agent` movido para `langchain.agents` (não crítico)

### Quality Gate

```bash
uv run python scripts/quality_gate.py
```

**Resultado:** `tudo passou`

| Check | Status |
|-------|--------|
| connector_validation_matrix | PASS |
| connector_coverage | PASS (76 lacunas informativas) |
| rag_dataset_schema | PASS |
| corpus_coverage | PASS |
| dataset_difficulty_mix | PASS |
| reranker_invariant | WARN (não significativo, não bloqueante) |
| promptfoo_configs | PASS |
| llm_baseline | PASS |
| deployed_model_evaluated | WARN (produção usa gpt-4o-mini, baseline qwen3.8:27b) |
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

## Serviços Externos Necessários

| Serviço | Status | Observação |
|---------|--------|------------|
| Ollama | Local | API em `http://127.0.0.1:11434` (host.docker.internal no Docker) |
| Qdrant | Opcional | Necessário para RAG integrado |
| Neo4j | Opcional | Necessário para GraphRAG |
| Redis | Opcional | Necessário para worker assíncrono |
| PostgreSQL | Opcional | Necessário para persistência de incidentes |

## Limitações Conhecidas

1. **Reranker invariant:** Não há melhoria estatisticamente significativa sobre o baseline atual (n=18, p=1.0). Não bloqueante.
2. **Deployed model:** Produção usa `gpt-4o-mini`, mas baseline mediu `qwen3.8:27b`. Apenas documentação/monitoramento.
3. **Index manifest:** Falta `sap_reference_library`. Não bloqueante para funcionalidade principal.

## Comandos Executados

```bash
mkdir -p reports/remediation
git rev-parse HEAD && git branch --show-current && git status --short
python3 --version && uv --version && node --version && npm --version
docker --version && docker compose version
uv sync --frozen
uv run pytest -m "not integration" --tb=short -q
uv run python scripts/quality_gate.py
```

## Próximos Passos

- Fase 1: Harness de avaliação de modelos e Promptfoo
- Fase 2: Eventos, filas e idempotência
- Fase 3: RAG — ingestão e consistência do índice
- Fase 4: RAG — recuperação, ranking e abstenção
- Fase 5: Contexto, geração e evidência
- Fase 6: Ontologia e integração com Neo4j
- Fase 7: Human-in-the-loop e verificação humana
- Fase 8: Segurança dos agentes, MCP e A2A
- Fase 9: Contratos, estados e persistência
- Fase 10: Resiliência, observabilidade e configuração
- Fase 11: Deployment, supply chain e hardening
- Fase 12: Manutenibilidade e arquitetura
- Fase 13: Backlog transversal
- Fase 14: Matriz de rastreabilidade
- Fase 15: Testes de regressão e quality gates
- Fase 16: Relatório final

---

**Status:** Baseline completo. Ambiente validado. Pronto para execução das fases subsequentes.
