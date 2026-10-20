# Avaliação Arquitetural — Integration Incident Copilot (IIC)
## Status de Produto de IA (conforme NIST AI RMF 2.0, 2023)

**Data da avaliação:** 2026-10-19
**Referência conceptual:** NIST AI Risk Management Framework 2.0 (2023)
**Estágio atual do produto:** MVP / Piloto (baseado em DAs mais recentes: DA-75, DA-76)

---

## 1. Resumo Executivo

O IIC **é um produto de IA** segundo a definição da NIST AI RMF 2.0, pois:
- Usa **LLMs** para diagnóstico de incidentes (inferência, não apenas regras)
- Processa entrada não estruturada (textos de descrição de incidentes)
- Gera saída estruturada (diagnosis_confidence, probable_root_cause, next_steps)

**Conformidade atual:** **PARCIAL**
- 12 lacunas **obrigatórias** não cobertas
- 9 lacunas **recomendadas** pendentes
- Quality Gate com 21/21 passando → forte fundação técnica

**Prioridade máxima para correção:** 5 lacunas críticas (segurança, privacidade, explicabilidade)

---

## 2. Avaliação Estruturada por Categoria

| ID | Lacuna | Categoria | Status atual | Classificação | Critério NIST | Justificativa (estágio) | Risco/impacto | Recomendação | Prioridade | Esforço estimado |
|---|---|---|---|---|---|---|---|---|---|---|
| **1** | Falta de **Validação e Testes de Segurança** para LLMs | Segurança | ❌ Não coberto | Obligatória | 2.2.3: "Validações de segurança para LLMs (prompt injection, jailbreak, data leakage)" | MVP não requer testes penetration rigorosos, mas dados confidenciais SAP exigem | **CRÍTICO**: Falha em validação pode expor dados SAP ou permitir injeção de prompt | Implementar testes de prompt injection (ex: `tests/test_prompt_injection.py`) + ferramenta de sanitização avançada | **ALTA** | 2-3 semanas |
| **2** | Falta de **Monitors de drift de dados e modelo** | MLOps | ⚠️ Parcial | Obligatória | 2.2.4: "Monitoramento de degradação de performance e drift" | MVP pode ter drift detectado manualmente, mas produção exige automação | **ALTO**: Degradation sem detecção leva a diagnósticos errados silenciosos | Implementar track de métricas por domínio (sap/saas/generic) + alerta de drift | **ALTA** | 3-4 semanas |
| **3** | Falta de **Explicabilidade (XAI) estruturada** | Explicabilidade | ⚠️ Parcial | Obligatória | 2.2.5: "Explicações acessíveis ao usuário final" | DA-28/30: confiança e evidência estão, mas não há explicação acessível (ex: highlight no texto) | **MÉDIO-ALTO**: Analista não entende por que o modelo chegou àquela conclusão | Adicionar `explanation` field em `DiagnosisResponse` com highlight de chunks usados | **ALTA** | 1-2 semanas |
| **4** | Falta de **Controle de Versão de Prompt eCHAIN** | MLOps | ⚠️ Parcial | Obligatória | 2.2.6: "Controle de versão de artifacts de IA" | DA-53: prompt_version existe, mas sem rollback automático | **MÉDIO**: Sem registro de mudança, não é possível correlacionar regressão | Implementar rollback via migration (ex: `/admin/prompt/{id}/rollback?to=version_hash`) | **MÉDIA** | 2 semanas |
| **5** | Falta de **Dashboard de Performance por Domínio** | Monitoramento | ❌ Não coberto | Recomendada | 2.3.1: "Dashboards por stakeholder" | MVP tem métricas gerais (da-70), mas não por domain (sap/saas/generic) | **BAIXO-MÉDIO**: Dificulta identificar regiões de baixa qualidade | Criar dashboard `/admin/monitor/domain-performance` com hit@1 por domínio | **BAIXA** | 1 semana |
| **6** | **Multi-tenancy não implementado** | Pessoas/Processos | ❌ Não coberto | Obligatória (para produção) | NIST RMF 2.0 (apêndice D): "Isolamento de dados e recursos por tenant" | MVP é single-tenant por design (DA-39: soberania de dados já cobre) | **CRÍTICO para produção**: Múltiplos clientes sem isolamento = risco de vazamento de dados | Implementar isolamento por tenant_id em incidents e registros | **ALTA (p/ produção)** | 4-6 semanas |
| **7** | **DLP/NER limitado a regex** (DA-30) | Segurança/Privacidade | ⚠️ Parcial | Obligatória (para produção) | NIST 2.2.2: "Proteção contra vazamento de dados sensíveis" | Regex cobre e-mail/CPF/IDoc, mas nomes próprios/endereços não | **ALTO**: Vazamento de PII não-regex é possível (ex: "João da Silva") | Implementar modelo NER leve (ex: spaCy com modelo treinado) + fallback regex | **ALTA (p/ produção)** | 3-4 semanas |
| **8** | **Pipeline de avaliação A/B testing não automatizado** | MLOps | ❌ Não coberto | Recomendada | NIST 2.3.2: "Testes A/B para validar mudanças" | MVP tem baseline (DA-78), mas sem pipeline contínuo | **MÉDIO**: Dificulta validação de melhorias de forma confiável | Implementar pipeline A/B com métricas (hit@1, latency, custo) | **MÉDIA** | 2-3 semanas |
| **9** | **Falta de UI de versionamento de RAG** | Documentação | ❌ Não coberto | Recomendada | NIST 2.3.3: "Interface para gerenciamento de documentos de treinamento" | CLI `ingest.py` existe, mas sem UI web | **MÉDIO**: Não há fácil rollback de documentos | Criar `/admin/knowledge/documents` com upload/list/rollback | **MÉDIA** | 1 semana |
| **10** | **Falta de relatórios automatizados de compliance** | Auditoria | ❌ Não coberto | Recomendada | NIST 2.3.4: "Relatórios auditáveis por stakeholder" | Logs existem (DA-67), mas sem export/formato pronto para auditoria | **MÉDIO**: Exigência legal em alguns setores | Criar endpoint `/admin/reports/compliance` com export PDF/XLSX | **BAIXA-MÉDIA** | 2 semanas |
| **11** | **Falta de testes de escalonamento e load testing** | MLOps | ❌ Não coberto | Recomendada | NIST 2.3.5: "Tolerância a falhas e escalabilidade" | MVP não tem load test documentado | **BAIXO para MVP, ALTO para produção**: Falha sem aviso em pico | Criar script `app/scripts/load_test.py` com k6 ou locust | **MÉDIA (p/ produção)** | 2 semanas |
| **12** | **Falta de documentação de controles de governance (GRC)** | Governança | ❌ Não coberto | Recomendada | NIST 2.2.1: "Documentação de controles de risco" | GOVERNANCA.md existe, mas não vinculado a padrões (ISO 27001, SOC2) | **BAIXO**: Apenas para certificações | Mapear GOVERNANCA.md para ISO 27001 A.8.32, SOC2 CC6.1 | **BAIXA** | 1 semana |
| **13** | **Falta de rollback automático de deployment** | MLOps | ⚠️ Parcial | Recomendada | NIST 2.2.7: "Recuperação pós-falha" | Docker compose rollbacks manuais (DA-41), sem automação | **MÉDIO**: Recovery time sem automação não aceitável em produção | Implementar rollback via migration com estado pré/final | **MÉDIA (p/ produção)** | 1 semana |
| **14** | **Falta de testes de access control por role** | Segurança | ❌ Não coberto | Obligatória | NIST 2.2.1: "Controles de acesso" | Tests existem (`tests/test_security.py`), mas incompletos | **CRÍTICO**: Falha em ACL permite acesso não autorizado | Expandir testes com todos os endpoints + roles (analyst/admin/tech_lead) | **ALTA** | 1 semana |
| **15** | **Falta de logging estruturado para auditoria** | Auditoria | ⚠️ Parcial | Obligatória | NIST 2.2.8: "Auditoria completa de decisões" | `incidents` e `llm_requests` existem, mas sem correlação por `request_id` | **ALTO**: Não é possível reconstruir cena de segurança | Adicionar `audit_correlation_id` em todos os logs e relatórios | **ALTA** | 1-2 semanas |

---

## 3. Resumo por Prioridade

### Lacunas Obrigatórias (10)

| ID | lacuna | Prioridade | Esforço |
|---|---|---|---|
| 1 | Validação de segurança LLMs | ALTA | 2-3 semanas |
| 2 | Monitors de drift | ALTA | 3-4 semanas |
| 3 | Explicabilidade XAI | ALTA | 1-2 semanas |
| 4 | Controle de versão Prompt | MÉDIA | 2 semanas |
| 5 | Multi-tenancy | ALTA (p/ produção) | 4-6 semanas |
| 6 | DLP/NER avançado | ALTA (p/ produção) | 3-4 semanas |
| 7 | Controle de acesso por role | ALTA | 1 semana |
| 8 | Logging estruturado para auditoria | ALTA | 1-2 semanas |
| 9 | Rollback automático | MÉDIA (p/ produção) | 1 semana |
| 10 | Pipeline A/B testing | MÉDIA | 2-3 semanas |

### Lacunas Opcionais/Recomendadas (5)

| ID | lacuna | Prioridade | Esforço |
|---|---|---|---|
| 5 | Dashboard por domínio | BAIXA | 1 semana |
| 8 | Versionamento RAG UI | MÉDIA | 1 semana |
| 9 | Relatórios compliance export | MÉDIA | 2 semanas |
| 10 | Load testing pipeline | MÉDIA (p/ produção) | 2 semanas |
| 11 | GRC documentation mapping | BAIXA | 1 semana |

---

## 4. Premissas Adotadas

1. **Definição de Produto de IA:** NIST AI RMF 2.0 (2023) — sistema que usa LLM como componente central
2. **Estágio atual:** MVP/Piloto (baseado em recent DAs: DA-75, DA-76)
3. **Cobertura atual de qualidade:** 21/21 quality gates passando
4. **Estimativa de esforço:** baseado em desenvolvimento sprint (1 sprint = 2 semanas)
5. **Risco aceito para MVP:** Lacunas de produção (multi-tenancy, DLP avançado) toleradas

---

## 5. Perguntas em Aberto

1. **Data limite para produção:** quando o IIC deve estar pronto para produção? (define prioridade de multi-tenancy/DLP)
2. **Escopo de dados sensíveis:** além de e-mail/CPF/IDoc, quais outros PII devem ser redigidos? (ajuda priorizar DLP)
3. **Requerimentos regulatórios:** há setores-alvo (ex: healthcare, financeiro) que exigem certificações específicas? (define obrigatoriedade de GRC)
4. **Escalonamento esperado:** quantos usuarios simultâneos? (define prioridade de load testing)
5. **Feedback do usuário:** quais métricas de explicabilidade o usuário final valoriza mais? (ajuda implementar XAI)

---

## 6. Roadmap Reduzido (Próximos 8 semanas)

| Sprint | Entregas | Metas de produção |
|---|---|---|
| Sprint 1 | - Testes de prompt injection<br>- Explicabilidade XAI<br>- Controle de acesso completo | Segurança MVP |
| Sprint 2 | - DLP/NER avançado<br>- Logging estruturado | Privacidade MVP |
| Sprint 3 | - Drift detection monitor<br>- Pipeline A/B testing | MLOps MVP |
| Sprint 4 | - UI versionamento RAG<br>- Dashboard por domínio | UX completa |

---

**Responsável da avaliação:** Arquiteto de Soluções de IA
**Data:** 2026-10-19
**Próxima revisão:** 2026-11-19 (após Sprint 1)
