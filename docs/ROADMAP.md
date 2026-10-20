# ROADMAP — Integration Incident Copilot

## Contexto

Este documento descreve o plano de execução para o Integration Incident Copilot (IIC), priorizando entregas com base em impacto, complexidade e dependências externas.

---

## 1. Entregas concluídas (2026-10-19)

| DA | Item | Status | Detalhes |
|---|---|---|---|
| DA-75 | Human-in-the-Loop | ✅ | Interrupt + endpoint `/incidents/{id}/review` |
| DA-76 | Aviso de IA | ✅ | Chave `ai_warning` em catálogo + `DiagnosisResponse.ai_warning` |
| — | Dossiê de Governança | ✅ | `docs/GOVERNANCA.md` criado |
| — | Threat model + security suite | ✅ | `docs/SECURITY.md`, `tests/test_security.py` |

---

## 2. Atividades planejadas

### Prioridade Alta (bloqueadas externamente)

| # | Atividade | Bloqueio | Estimativa |
|---|---|---|---|
| 7 | Correção do TCO | Precisa docs SAP HANA Cloud/AI Core | 2-3h |
| 8 | Comparação de modelos (promptfoo) | Provider provider com config DB | 3-4h |

### Prioridade Média (implementáveis hoje)

| # | Atividade | Bloqueio | Estimativa |
|---|---|---|---|
| 9 | UI de gestão de RAG | Nenhuma | 2h |
| 10 | Framework A/B testing para modelos | Nenhuma | 3h |
| 11 | Validação em cluster real (k3d) | CI/CD setup | 4h |

### Prioridade Baixa (backlog)

| # | Atividade | Bloqueio | Estimativa |
|---|---|---|---|
| 12 | Multi-tenancy support | Arquitetura | Sem prazo |
| 13 | DLP/NER avançado | ML model training | Sem prazo |
| 14 | Sanitização web search (anti-injection) | DA-71 limitação conhecida | Sem prazo |

---

## 3. Próximas ações imediatas

### Passo 1: Fix promptfoo provider (Prioridade média)
**Objetivo:** Habilitar comparação de modelos via promptfoo

**Tarefas:**
- [ ] Investigar configuração DB (`llm_runtime_config` campos NULL)
- [ ] Corrigir `scripts/promptfoo_provider.py` para configurar defaults se dados faltarem
- [ ] Testar com Ollama localhost
- [ ] Atualizar `docs/DECISOES_DE_ARQUITETURA.md` com resultado (DA-77)

**Saída:** `promptfoo eval -c promptfooconfig.compare.yaml` rodando com múltiplos modelos

### Passo 2: UI de gestão de RAG (Prioridade média)
**Objetivo:** Substituir CLI `app/rag/ingest.py` por interface web

**Tarefas:**
- [ ] Endpoints `/admin/knowledge/documents` (GET/POST/DELETE)
- [ ] Upload de documentos Markdown
- [ ] Listar/verificar status do ingest
- [ ] Rollback para versão anterior (se necessário)

**Tecnologia:** FastAPI + React (usar mesmo padrão de Admin UI)

### Passo 3: Framework A/B testing (Prioridade média)
**Objetivo:** Comparar versões de prompt/models com métricas consistentes

**Tarefas:**
- [ ] Endpoint `/admin/models/compare` com parâmetros:
  - `variant_a`, `variant_b` (prompt versions ou models)
  - `test_set` (baseline, eval, custom)
  - `samples` (quantidade de testes)
- [ ] Pipeline de execução paralela
- [ ] Dashboard com métricas: hit@1, precision, recall, latency
- [ ] Relatório com conclusão estatística (chi-square ou t-test)

**Tecnologia:** LangChain + pytest + Plotly dashboard

### Passo 4: Validação k3d (Prioridade baixa)
**Objetivo:** Testar deploy completo antes de produção

**Tarefas:**
- [ ] Script `scripts/validate_k3d.py` executando smoke tests
- [ ] CI/CD job `validate-k3d` no GitHub Actions
- [ ] Verificar: postgres up, Ollama disponível, Qdrant indexado
- [ ] Teste de diagnóstico completo end-to-end

---

## 4. Roadmap por sprint (sugestão)

### Sprint 1 (2 sprints para completion)

| Sprint | Objetivo | Delivery |
|---|---|---|
| Sprint A | Promptfoo fix + UI RAG básica | Comparação de modelos habilitada, upload de docs via UI |
| Sprint B | A/B testing + k3d validation | Dashboard de métricas, deploy validado |

### Sprint 2 (features de alto impacto)

| Sprint | Objetivo | Delivery |
|---|---|---|
| Sprint C | DLP/NER com ML model | Redação de PII avançada (nomes, endereços) |
| Sprint D | Auto-retroalimentação do dataset | Dataset RAG auto-atualizado com casos novos |

---

## 5. Indicadores de sucesso

| Métrica | Meta | Como medir |
|---|---|---|
| Hit@1 baseline | ≥90% | `promptfoo eval --filter-metadata path=llm` |
| Tempo de diagnóstico (p95) | ≤15s | `diagnosis_duration_seconds` no Prometheus |
| UI de RAG usada | ≥10 uploads/semana | Logs do Admin UI |
| Comparison A/B testado | ≥1x/semana | Endpoint `/admin/models/compare` usage |
| Deploy k3d validado | 100% dos merges | CI/CD status |

---

## 6. Status atual (2026-10-19)

**Impedimentos:**
- DA-77 (correção do TCO) bloqueada por necessidade de documentação SAP externa
- DA-78 (comparação de modelos) bloqueada por configuração DB do promptfoo provider

**Próxima atualização:** 2026-10-20 após tentativa de fix promptfoo provider

---

**Responsável:** Tech Lead
**Revisão:** Semanal
**Conteúdo:** Proposta de execução com base em `docs/GOVERNANCA.md` e backlog identificado
