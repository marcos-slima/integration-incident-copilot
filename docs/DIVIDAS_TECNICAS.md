# DÍVIDAS TÉCNICAS — Integration Incident Copilot
## Status: 2026-10-19

**Data da análise:** 19/10/2026
**Referência:** Avaliação Arquitetural (`docs/AVALIACAO_ARQUITETURAL_PRODUTO_AI.md`)
**Estágio atual:** MVP/Piloto (21/21 quality gates passando)

---

## 1. Resumo Executivo

Este documento lista as dívidas técnicas identificadas no IIC com base na Avaliação Arquitetural NIST AI RMF 2.0.

**Total de dívidas:** 15
**Dívidas críticas (bloqueiam produção):** 6
**Dívidas técnicas (removem barreiras a evolução):** 9

**Esforço total estimado:** ~25 semanas (6 meses)

---

## 2. Dívidas Críticas (Bloqueiam Produção)

| ID | Dívida | Categoria | Status | Esforço | Impacto |
|---|---|---|---|---|---|
| **DA-77** | Validade de Testes de Segurança LLMs | Segurança | ⚠️ Testes implementados | 2-3 semanas | Falha em produção pode expor dados SAP |
| **DA-78** | Monitors de drift de modelo/dados | MLOps | ⚠️ Testes implementados | 3-4 semanas | Degradation silenciosa → diagnósticos errados |
| **DA-79** | Multi-tenancy não implementado | Pessoas/Processos | ❌ Pendente | 4-6 semanas | **CRÍTICO:** Vazamento de dados entre tenants |
| **DA-80** | DLP/NER avançado (não-regex) | Segurança/Privacidade | ❌ Pendente | 3-4 semanas | Vazamento de PII não-regex (nomes, endereços) |
| **DA-81** | Logging estruturado com audit_correlation_id | Auditoria | ⚠️ Testes implementados | 1 semana | Não é possível reconstruir cenários de segurança |
| **DA-82** | Rollback automático de deployment | MLOps | ⚠️ Parcial | 1 semana | Recovery time alto sem automação |

---

## 3. Dívidas Técnicas (Removem Barreiras a Evolução)

| ID | Dívida | Categoria | Status | Esforço | Impacto |
|---|---|---|---|---|---|
| **DA-83** | Explicabilidade XAI estruturada | Explicabilidade | ⚠️ Testes implementados | 1-2 semanas | Analista não entende por que o modelo chega à conclusão |
| **DA-84** | Controle de Versão de Prompt | MLOps | ⚠️ Parcial | 2 semanas | Não é possível correlacionar regressão |
| **DA-85** | Pipeline A/B testing automatizado | MLOps | ❌ Pendente | 2-3 semanas | Validação de melhorias não é confiável |
| **DA-86** | UI de versionamento de RAG | Documentação | ❌ Pendente | 1 semana | Sem rollback fácil de documentos |
| **DA-87** | Dashboard de Performance por Domínio | Monitoramento | ✅ Implementado | 1 semana | Dificulta identificar regiões de baixa qualidade |
| **DA-88** | Testes de escalonamento e load testing | MLOps | ❌ Pendente | 2 semanas | Falha sem aviso em pico |
| **DA-89** | Relatórios automatizados de compliance | Auditoria | ✅ Implementado | 2 semanas | Exigência legal em alguns setores |
| **DA-90** | Documentação de controles GRC (ISO 27001, SOC2) | Governança | ✅ Implementado | 1 semana | Certificações não são possíveis |
| **DA-91** | Expansão dos testes de RBAC para todos os endpoints | Segurança | ⚠️ Parcial | 1-2 semanas | Testes incompletos deixam brechas |

---

## 4.Status das Dívidas Identificadas

### Dívidas Concluídas (Hoje)

| ID | Dívida | Status | Data | Obs |
|---|---|---|---|---|
| DA-76 | Aviso de IA | ✅ | 19/10/2026 | Implementado |
| DA-75 | Human-in-the-Loop | ✅ | 19/10/2026 | Implementado |
| DA-77 | Validade de Testes de Segurança LLMs | ⚠️ | 19/10/2026 | Testes implementados (faltam mocks) |
| DA-78 | Monitors de drift | ⚠️ | 19/10/2026 | Testes implementados |
| DA-81 | Logging estruturado | ⚠️ | 19/10/2026 | Testes implementados |

### Dívidas Pendentes

| ID | Dívida | Prioridade | Sprint |
|---|---|---|---|
| DA-79 | Multi-tenancy | ALTA (produção) | Sprint 3+ |
| DA-80 | DLP/NER avançado | ALTA (produção) | Sprint 2+ |
| DA-83 | Explicabilidade XAI | ALTA | Sprint 1+ |
| DA-84 | Controle de Versão Prompt | MÉDIA | Sprint 1+ |
| DA-85 | Pipeline A/B testing | MÉDIA | Sprint 3+ |
| DA-86 | UI versionamento RAG | MÉDIA | Sprint 4+ |
| DA-87 | Dashboard por domínio | BAIXA | Sprint 4+ |
| DA-88 | Load testing | MÉDIA (produção) | Sprint 3+ |
| DA-89 | Relatórios compliance | BAIXA-MÉDIA | Sprint 4+ |
| DA-90 | Documentação GRC | BAIXA | Sprint 4+ |
| DA-91 | Testes RBAC completos | ALTA | Sprint 1+ |

---

## 5. Roadmap de Correção (Sprints)

### Sprint 1 (Segurança MVP)
| ID | Dívida | Entregas |
|---|---|---|
| DA-83 | Explicabilidade XAI | Field `explanation` em `DiagnosisResponse` |
| DA-91 | Testes RBAC completos | Endpoints: `/diagnose`, `/admin/*`, `/mcp`, `/a2a` |
| DA-84 | Controle de Versão Prompt | Endpoint `/admin/prompt/{id}/rollback` |

### Sprint 2 (Privacidade MVP)
| ID | Dívida | Entregas |
|---|---|
| DA-80 | DLP/NER avançado | Modelo NER spacy + fallback regex |

### Sprint 3 (MLOps MVP)
| ID | Dívida | Entregas |
|---|---|
| DA-85 | Pipeline A/B testing | `/admin/models/compare` com métricas |
| DA-77 | Validação LLM security | Mocks para prompt injection |
| DA-88 | Load testing | `scripts/load_test.py` com k6 |

### Sprint 4 (UX Completa)
| ID | Dívida | Entregas |
|---|---|
| DA-86 | UI versionamento RAG | `/admin/knowledge/documents` |
| DA-87 | Dashboard por domínio | Hit@1 por domain (sap/saas/generic) |
| DA-89 | Relatórios compliance | `/admin/reports/compliance` export PDF/XLSX |
| DA-90 | Documentação GRC | Mapeamento ISO 27001, SOC2 |

---

## 6. Estimativa de Esforço Total

| Categoria | Esforço (semanas) |
|---|---|
| Segurança | 2 semanas (DA-91) + 2 semanas (DA-77) = 4 semanas |
| Privacidade | 3-4 semanas (DA-80) |
| MLOps | 2 semanas (DA-85) + 2 semanas (DA-88) + 1 semana (DA-77) = 5 semanas |
| Governance | 1-2 semanas (DA-84) + 1 semana (DA-90) = 2 semanas |
| Monitoring | 1 semana (DA-83) + 1 semana (DA-87) = 2 semanas |
| Documentation | 1 semana (DA-86) = 1 semana |
| **Total** | **~15-16 semanas** (3-4 meses) |

---

## 7. Recomendações

1. **Priorizar DA-79 (Multi-tenancy)** antes da produção se houver múltiplos clientes
2. **Priorizar DA-80 (DLP/NER avançado)** se houver dados sensíveis além de e-mail/CPF/IDoc
3. **Fazer DA-83 (XAI)** primeiro para facilitar diagnósticos de erro
4. **Implementar DA-91 (RBAC completo)** antes de any security-sensitive features

---

**Responsável:** Arquiteto de Soluções de IA
**Data:** 2026-10-19
**Próxima revisão:** 2026-11-19 (após Sprint 1)
