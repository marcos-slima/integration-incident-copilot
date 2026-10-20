# GOVERNANÇA — Integration Incident Copilot

## 1. Visão geral

Este documento estabelece as políticas, responsabilidades e controles de governança para o projeto **Integration Incident Copilot (IIC)**.

---

## 2. Princípios de governança

| Princípio | Descrição |
|---|---|
| **Transparência** | Todas as decisões de arquitetura (DAs) são publicamente registradas em `docs/DECISOES_DE_ARQUITETURA.md`. |
| **Qualidade como regra** | Quality gates obrigatórios em CI/CD (DA-51: 21 invariantes). Falha → build bloqueado. |
| **Rastreabilidade** | Cada DA vincula código → documentação → testes → migrations. |
| **Resiliência** | Fail-closed em tudo (policy engine, capability registry, web search). |
| **Soberania de dados** | Dados de cliente nunca saem da região autorizada (DA-39, `strict`/`cloud_with_dlp`). |

---

## 3. Papéis e responsabilidades

| Papel | Responsabilidades |
|---|---|
| **Tech Lead / Arquiteto** | Approvar DAs, definir stack técnica, revisar roadmap, monitorar quality gates. |
| **Engenheiro desoftware** | Implementar DAs, escrever tests + quality gates, manter migrations, atualizar documentação. |
| **QA Engineer** | Validar quality gates, executar testes de integração, monitorar cobertura de testes. |
| ** DevOps** | Gerenciar infra (compose, docker), monitorar dashboards, incident response. |
| **Product Owner** | Priorizar backlog, definir critérios de aceite, definir escopo de release. |

---

## 4. Processo de desenvolvimento

### 4.1. Flujo de trabalho

```
1. Backlog → Priorização (PO)
2. Tarefa → Design (DA) → Implementação
3. Testes (unit/integration) + Quality Gate
4. Code review + Merge
5. Deploy (CI/CD)
```

### 4.2. Regras de code review

- **Obrigatório**: 2 approvals para `main`.
- **Obrigatório**: Quality gate passando (da_registered, implemented_das_documented, etc.).
- **Obrigatório**: Testes unitários > 80% (cobertura).
- **Proibido**: `sk-` prefix em any test value (triggers gitleaks).
- **Obrigatório**: Pre-commit hooks (ruff, ruff-format, gitleaks, trim-whitespace).

### 4.3. Versão do código

- Branch `main` → sempre release-ready.
- Branch `dev` → feature integration.
- Tags semânticas: `vX.Y.Z` (ex: `v1.4.0`).
- Tags são imutáveis: nunca reutilize nem mova uma tag já publicada. Se o
  número existir, use o próximo minor/patch e registre o motivo no
  `CHANGELOG.md` (foi o que aconteceu com a tag experimental `v1.3.0`, que
  levou a release de 2026-10-20 a ser `v1.4.0`).
- Migrations: sequencial, idempotent, `alembic upgrade head` antes de deploy.

---

## 5. Gestão de decisões de arquitetura (DA)

### 5.1. Processo de criação

1. **Identificação** → problema técnico com impacto arquitetural.
2. **Proposta** → documento em `docs/DECISOES_DE_ARQUITETURA.md` (motivo, decisão, implementação, validação, limitações).
3. **Implementação** → código + migration + testes.
4. **Validação** → quality gates e CI/CD.
5. **Registro** → DA na tabela de `../CLAUDE.md` e `DECISOES_DE_ARQUITETURA.md` (índice).

### 5.2. Critério de aprovação

- **Tech Lead** aprova DA (não pode ser auto-aprovado).
- **Equipe de engenharia** revisa antes do merge.
- **Pre-commit** valida DA (gate `da_registered` e `implemented_das_documented`).

### 5.3. DA candidatas (não implementadas)

- DA-31: SAP AI Agent Hub registration (bloqueada por Kyma).
- DA-64: número reservado (não usado intencionalmente).
- DA-76: implementada em 2026-10-07 (aviso de IA).

---

## 6. Compliance e auditoria

### 6.1. Logs e rastreabilidade

| Camada | Dado rastreado | Retenção | Access control |
|---|---|---|---|
| **LLM calls** | provider, model, tokens, duration | 90 dias | Sudo:Tech Lead |
| **AI Gateway** | routes, budget, failover | 180 dias | Sudo:Tech Lead |
| **Incidents** | request/response, diagnosis, escalation | 7 anos (legado SAP) | Role-based (admin/analyst) |
| **Audit log** | who/what/when/where | 7 anos | Role-based (admin only) |

### 6.2. Regras de segurança

- **Secrets** nunca em código ou logs (MCP: Fernet em repouso).
- **PII** redaction antes do prompt (DA-30).
- **Rate limiting** em todos os endpoints (10/min para `/review`).
- **Circuit breaker** por origem (DA-41).

### 6.3. Vulnerabilidade

| Nível | Critério | Ação |
|---|---|---|
| **Crítico** | Exposição de segredos, bypass de auth | Hotfix + alerta imediato |
| **Alto** | SQL injection, XSS, CSRF | Priorizar no sprint |
| **Médio** | ERD não validado, logging de dados sensíveis | Backlog do roadmap |
| **Baixo** | Pequeno typo, UX polido | Backlog informativo |

---

## 7. Quality gates (DA-51)

O project tem 21 quality gates (todos obrigatórios para merge):

| Gate | Falha → bloqueio? | Descrição |
|---|---|---|
| `implemented_das_documented` | Sim | Cada DA citada no código tem prosa em `DECISOES_DE_ARQUITETURA.md`. |
| `da_registered` | Sim | Cada DA citada tem linha na tabela de `CLAUDE.md`. |
| `das_index_current` | Sim | Índice de `DECISOES_DE_ARQUITETURA.md` contém todas as DAs. |
| `prompt_digest_measured` | Sim | Prompt `pt-BR` digest coincide com `data/eval/promptfoo_baseline.json`. |
| `connector_reachable` | Sim | Conector tem todos os 9 requisitos (registry, Literals, routes, CLI, etc.). |
| `connector_coverage` | Informativo | `data/connector_coverage.yaml` deve Cobrir todas as 27 SAP products × 9 mechanisms. |
| `connector_validation_matrix` | Sim | `docs/ARCHITECTURE.md` cobre conector. |
| `candidate_das_fresh` | Sim | DAs candidatas não têm prosa (ainda não implementadas). |
| `ui_i18n` | Sim | UI web e Admin UI traduzem todos os códigos. |
| `content_i18n` | Sim | Catálogo `pt-BR` / `en` completo. |
| `docs_markup_integrity` | Informativo | Markdown sem tags não permitidas. |
| `docs_code_references` | Informativo | Links para código estão rotos. |
| `docs_env_vars` | Informativo | Variáveis de ambiente documentadas. |
| `reranker_invariant` | Informativo | `mmarco-mMiniLMv2-L12-H384-v1` é baseline (DA-29). |
| `deployed_model_evaluated` | Informativo | Modelo em produção comparado ao baseline. |
| `llm_baseline` | Informativo | Baseline medido com `promptfoo`. |
| `promptfoo_configs` | Informativo | Arquivos de configuração consistentes. |
| `index_manifest` | Informativo | `data/index_manifest.json` atualizado. |
| `rag_dataset_schema` | Informativo | Dataset RAG schema válido. |
| `corpus_coverage` | Informativo | Documentos RAG cobrem domínios principais. |
| `dataset_difficulty_mix` | Informativo | Dataset tem casos simples/complexos. |

**Execução:** `PATH="$PWD/.venv/bin:$PATH" uv run python scripts/quality_gate.py`

---

## 8. Deploy e release

### 8.1. CI/CD

- **Build** → `docker build -t iic:latest .`
- **Test** → `uv run pytest tests/ -m "not integration" -v --cov=app --cov-fail-under=80`
- **Quality gate** → `uv run python scripts/quality_gate.py`
- **Deploy** → `docker compose up -d --build`

### 8.2. Rollback

- **Migrations** → `alembic downgrade -1`
- **Imagem** → `docker pull iic:<previous_tag>` + `docker compose up -d --build`
- **Revert** → `git revert <commit>` (não squash)

---

## 9. Incident response

### 9.1. Escalonamento

| Nível | Descrição | Prazo | Responsável |
|---|---|---|---|
| **Tier 1** | Erro de conn. (fallback → rule engine) | Auto | System |
| **Tier 2** | Erro de LLM (fallback → cloud) | 15 min | DevOps |
| **Tier 3** | Erro de gateway (escalation → humano) | 5 min | Tech Lead |

### 9.2. Comunicação

- **Slack** → `#iic-alerts` para Tier 2+.
- **PagerDuty** → Tech Lead para Tier 3.
- **Incident report** → relatório pós-incidente (criar antes do deploy).

---

## 10. Manutenção e evolução

### 10.1. Technical debt

- **Inspecionar mensalmente** com `uv run ruff check app/`.
- **Revisar migrations** cada release.
- **Apagar cache antigo** (`.mypy_cache`, `.pytest_cache`, `.ruff_cache`).

### 10.2. Deprecation

- **Alert 30 dias antes**.
- **Migration guide** em `CHANGELOG.md`.
- **Version tag** com `-deprecated` flag no código.

### 10.3. Feature flag

- **Temporário** → `FEATURE_<name>=true/false` no `.env`.
- **Definitivo** → remover flag e código legacy.

---

## 11. Checklist de lançamento

Antes de cada release:

- [ ] Quality gate passando.
- [ ] Todos os testes passando.
- [ ] Migrations rodando (`alembic upgrade head`).
- [ ] Dashboards atualizados (`scripts/validate_dashboards.py`).
- [ ] `CHANGELOG.md` atualizado.
- [ ] Tag semântica criada (`vX.Y.Z`).
- [ ] Docker image buildada e testada.
- [ ] Documentação atualizada (`docs/`).
- [ ] `CLAUDE.md` e `DECISOES_DE_ARQUITETURA.md` consistentes.

---

## 12. Referências

- [Architecture Decision Records (ADR)](https://adr.github.io/)
- [Quality Gates (DA-51)](../CLAUDE.md)
- [Decisões de Arquitetura](./DECISOES_DE_ARQUITETURA.md#registrar-uma-nova-da)
- [GraphRAG (DA-21)](./DECISOES_DE_ARQUITETURA.md#21-graphrag-como-camada-de-conhecimento-operacional---hardening-da-21)
- [RAG (DA-25)](./DECISOES_DE_ARQUITETURA.md#24-evidencetrust-layer--correção-do-threshold-do-rag-antes-do-reranker-da-25)

---

**Última revisão:** 2026-10-19
**Próxima revisão:** 2026-11-19
**Responsável:** Tech Lead
