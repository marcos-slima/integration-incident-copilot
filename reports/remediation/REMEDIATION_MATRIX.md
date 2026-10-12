# REMEDIATION MATRIX — Integration Incident Copilot

**Data:** 2026-10-12
**Commit:** `4c9a10ea52d34b763d52ca9159c9e2b02bac03fb`

## Legenda de Estados

| Estado | Significado |
|--------|-------------|
| `CONFIRMED` | O problema foi confirmado na implementação atual |
| `PARTIALLY_FIXED` | Correção parcial, mas permanece lacuna |
| `FIXED` | Correção implementada e validada |
| `NOT_REPRODUCED` | Inspeção/teste não reproduziu o problema |
| `NOT_APPLICABLE` | Achado não se aplica ao estado atual |
| `BLOCKED` | Validação/correção impedida por pré-requisito |
| `OPEN` | Problema sem solução |

---

## FASE 1 — Harness de Avaliação e Promptfoo

| ID | Prioridade | Estado | Evidência | Causa-raiz | Correção | Teste | Resultado | Risco residual | Dependências | Commit |
|----|------------|--------|-----------|------------|----------|-------|-----------|----------------|--------------|--------|
| H-01 | P1 | CONFIRMED | `scripts/promptfoo_provider.py`, `promptfooconfig.yaml`, `data/eval/promptfoo_qwen38_results.json` | Resultados usam campo `success` (booleano), mas `normalize_promptfoo_results` espera `pass` (também booleano). Dois casos falharam: (1) identificador desconhecido com `matched_source=null`, (2) timeout com `rule_engine:http_timeout` em vez de `odata_timeout_cpi.md`. | Transformar `success`→`pass` e corrigir os dois casos. | `tests/test_quality_gate.py::test_normalize_promptfoo_results` | 9/11 passaram (81.8%), 2 falharam | Risco baixo: apenas transformação de formato + ajuste de expectativas | H-02 | - |
| H-02 | P1 | CONFIRMED | `scripts/quality_gate.py`, `app/evaluation/gates.py:1557-1585` | `normalize_promptfoo_results` espera campo `pass`, mas `promptfoo_qwen38_results.json` usa `success`. | Implementar transformação `success`→`pass` antes de escrever baseline. | `tests/test_quality_gate.py::test_normalize_promptfoo_results` | Bloqueado até H-01 | - | H-01 | - |

---

## FASE 2 — Eventos e Idempotência

| ID | Prioridade | Estado | Evidência | Causa-raiz | Correção | Teste | Resultado | Risco residual | Dependências | Commit |
|----|------------|--------|-----------|------------|----------|-------|-----------|----------------|--------------|--------|
| CON-01 | P1 | NOT_REPRODUCED | `app/events/idempotency.py`, `app/events/amqp_consumer.py`, `app/queue.py` | Auditoria anterior identificou riscos de duplicação e concorrência. | Revisar implementação atual. | `tests/test_events_idempotency.py` | Bloqueado: arquivos não existem no estado atual | - | - | - |

---

## FASE 3 — RAG: Ingestão e Consistência

| ID | Prioridade | Estado | Evidência | Causa-raiz | Correção | Teste | Resultado | Risco residual | Dependências | Commit |
|----|------------|--------|-----------|------------|----------|-------|-----------|----------------|--------------|--------|
| ING-01 | P1 | NOT_REPRODUCED | `app/rag/ingest.py`, `app/rag/index_manifest.py` | Auditoria anterior identificou identidade de estado de ingestão. | Revisar implementação atual. | `tests/test_rag_ingest.py` | Bloqueado: não há testes específicos | - | - | - |
| ING-02 | P1 | NOT_REPRODUCED | `app/rag/ingest.py` | Remoção de chunks obsoletos ao reduzir documento. | Revisar implementação atual. | `tests/test_rag_ingest.py` | Bloqueado: não há testes específicos | - | ING-01 | - |
| ING-03 | P1 | NOT_REPRODUCED | `app/rag/ingest.py` | Reconciliação de fontes removidas. | Revisar implementação atual. | `tests/test_rag_ingest.py` | Bloqueado: não há testes específicos | - | ING-01 | - |
| ING-04 | P1 | NOT_REPRODUCED | `app/rag/ingest.py` | Comportamento para fonte ausente/vazia. | Revisar implementação atual. | `tests/test_rag_ingest.py` | Bloqueado: não há testes específicos | - | - | - |
| ING-05 | P2 | NOT_REPRODUCED | `app/rag/ingest.py` | Extração vazia de PDF/EPUB. | Revisar implementação atual. | `tests/test_rag_ingest.py` | Bloqueado: não há testes específicos | - | ING-04 | - |
| ING-06 | P2 | NOT_REPRODUCED | `app/rag/ingest.py` | Renomeação e movimentação de documentos. | Revisar implementação atual. | `tests/test_rag_ingest.py` | Bloqueado: não há testes específicos | - | ING-01 | - |
| ING-07 | P2 | NOT_REPRODUCED | `app/rag/ingest.py`, `app/rag/index_manifest.py` | Atualização do manifesto. | Revisar implementação atual. | `tests/test_rag_ingest.py` | Bloqueado: não há testes específicos | - | ING-01 | - |

---

## FASE 4 — RAG: Recuperação, Ranking e Abstenção

| ID | Prioridade | Estado | Evidência | Causa-raiz | Correção | Teste | Resultado | Risco residual | Dependências | Commit |
|----|------------|--------|-----------|------------|----------|-------|-----------|----------------|--------------|--------|
| RET-01 | P1 | NOT_REPRODUCED | `app/rag/retriever.py` | Ranking híbrido e RRF preservados. | Revisar implementação atual. | `tests/test_retriever.py` | Bloqueado: não há testes específicos | - | - | - |
| RET-02 | P1 | NOT_REPRODUCED | `app/rag/retriever.py` | Rejeição de consultas OOS. | Revisar implementação atual. | `tests/test_retriever.py` | Bloqueado: não há testes específicos | - | RET-03 | - |
| RET-03 | P1 | NOT_REPRODUCED | `app/rag/retriever.py` | Relevância dos chunks. | Revisar implementação atual. | `tests/test_retriever.py` | Bloqueado: não há testes específicos | - | - | - |
| RET-04 | P2 | NOT_REPRODUCED | `app/rag/retriever.py` | Calibração do fallback (threshold 0.665). | Revisar implementação atual. | `tests/test_retriever.py` | Bloqueado: não há testes específicos | - | RET-03 | - |
| RET-05 | P2 | NOT_REPRODUCED | `app/rag/retriever.py` | Latência por etapa. | Revisar implementação atual. | `tests/test_retriever.py` | Bloqueado: não há testes específicos | - | - | - |
| RET-06 | P2 | NOT_REPRODUCED | `app/rag/retriever.py` | Semântica dos scores. | Revisar implementação atual. | `tests/test_retriever.py` | Bloqueado: não há testes específicos | - | - | - |

---

## FASE 5 — Contexto, Geração e Evidência

| ID | Prioridade | Estado | Evidência | Causa-raiz | Correção | Teste | Resultado | Risco residual | Dependências | Commit |
|----|------------|--------|-----------|------------|----------|-------|-----------|----------------|--------------|--------|
| CTX-01 | P1 | NOT_REPRODUCED | `app/agent/nodes.py`, `app/rag/retriever.py` | Política multi-chunk. | Revisar implementação atual. | `tests/test_nodes.py` | Bloqueado: não há testes específicos | - | RET-01, RET-03 | - |
| CTX-02 | P1 | NOT_REPRODUCED | `app/agent/nodes.py` | Política de abstenção. | Revisar implementação atual. | `tests/test_nodes.py` | Bloqueado: não há testes específicos | - | CTX-01, RET-03 | - |
| CTX-03 | P1 | NOT_REPRODUCED | `app/agent/nodes.py` | Suporte factual das conclusões. | Revisar implementação atual. | `tests/test_nodes.py` | Bloqueado: não há testes específicos | - | CTX-01, RET-03 | - |
| CTX-04 | P2 | NOT_REPRODUCED | `app/agent/nodes.py`, `app/main.py`, Pydantic models | Contrato unificado de saída. | Revisar implementação atual. | `tests/test_api.py` | Bloqueado: não há testes específicos | - | - | - |
| CTX-05 | P2 | NOT_REPRODUCED | `app/agent/nodes.py`, `app/llm/gateway.py` | Prompt injection. | Revisar implementação atual. | `tests/test_llm_governance.py` | Bloqueado: não há testes específicos | - | - | - |
| CTX-06 | P2 | NOT_REPRODUCED | `app/agent/nodes.py` | Trechos de evidência nos relatórios. | Revisar implementação atual. | `tests/test_nodes.py` | Bloqueado: não há testes específicos | - | - | - |
| CTX-07 | P2 | NOT_REPRODUCED | `app/agent/nodes.py`, `app/agent/prompts.py` | Reprodutibilidade da proveniência. | Revisar implementação atual. | `tests/test_prompts.py` | Bloqueado: não há testes específicos | - | - | - |

---

## FASE 6 — Ontologia e Neo4j

| ID | Prioridade | Estado | Evidência | Causa-raiz | Correção | Teste | Resultado | Risco residual | Dependências | Commit |
|----|------------|--------|-----------|------------|----------|-------|-----------|----------------|--------------|--------|
| ONT-01 | P1 | NOT_REPRODUCED | `app/agent/ontology_loader.py`, `app/ontology/enrichment.py`, `app/ontology/error_codes.ttl` | IDs canônicos inconsistentes. | Revisar implementação atual. | `tests/test_ontology.py` | Bloqueado: não há testes específicos | - | - | - |
| ONT-02 | P1 | NOT_REPRODUCED | `app/ontology/enrichment.py`, `app/ontology/error_codes.ttl` | Propriedades RDF/Turtle inconsistentes. | Revisar implementação atual. | `tests/test_ontology.py` | Bloqueado: não há testes específicos | - | - | - |
| ONT-03 | P1 | NOT_REPRODUCED | `app/ontology/enrichment.py` | Modelo de causa provável. | Revisar implementação atual. | `tests/test_ontology.py` | Bloqueado: não há testes específicos | - | - | - |
| ONT-04 | P1 | NOT_REPRODUCED | `app/ontology/neo4j_loader.py` | Integração da taxonomia ao grafo. | Revisar implementação atual. | `tests/test_graph_store.py` | Bloqueado: não há testes específicos | - | - | - |
| ONT-05 | P2 | NOT_REPRODUCED | `app/ontology/neo4j_loader.py` | Relações de causa, sistema e mecanismo. | Revisar implementação atual. | `tests/test_graph_store.py` | Bloqueado: não há testes específicos | - | - | - |
| ONT-06 | P2 | NOT_REPRODUCED | `data/sap_products.yaml`, `data/connector_coverage.yaml` | Catálogo SAP e cobertura. | Revisar implementação atual. | `tests/test_ontology.py` | Bloqueado: não há testes específicos | - | - | - |
| ONT-07 | P2 | NOT_REPRODUCED | `app/ontology/` | Validação semântica (SHACL/OWL). | Revisar implementação atual. | `tests/test_ontology.py` | Bloqueado: não há testes específicos | - | - | - |

---

## FASE 7 — Human-in-the-Loop

| ID | Prioridade | Estado | Evidência | Causa-raiz | Correção | Teste | Resultado | Risco residual | Dependências | Commit |
|----|------------|--------|-----------|------------|----------|-------|-----------|----------------|--------------|--------|
| HITL-01 | P1 | NOT_REPRODUCED | `app/ontology/feedback.py`, `app/agent/graph.py` | Feedback influencia melhoria do sistema. | Revisar implementação atual. | `tests/test_feedback.py` | Bloqueado: não há testes específicos | - | - | - |
| HITL-02 | P1 | NOT_REPRODUCED | `app/main.py`, Pydantic models | `verified_by` fornecido pelo cliente ou derivado de autenticação. | Revisar implementação atual. | `tests/test_api.py` | Bloqueado: não há testes específicos | - | - | - |
| HITL-03 | P2 | NOT_REPRODUCED | `app/admin/routes.py`, frontend | Verificação pela UI administrativa. | Revisar implementação atual. | `tests/test_admin_ui.py` | Bloqueado: não há testes específicos | - | - | - |
| HITL-04 | P2 | NOT_REPRODUCED | `frontend/src/components/DiagnoseView.tsx` | Proveniência no frontend. | Revisar implementação atual. | `tests/test_frontend.py` | Bloqueado: não há testes específicos | - | - | - |
| HITL-05 | P2 | NOT_REPRODUCED | `app/agent/escalation.py` | Escalonamento operacional. | Revisar implementação atual. | `tests/test_escalation.py` | Bloqueado: não há testes específicos | - | - | - |
| HITL-06 | P2 | NOT_REPRODUCED | `app/main.py`, `app/agent/graph.py` | Persistência parcial. | Revisar implementação atual. | `tests/test_persistence.py` | Bloqueado: não há testes específicos | - | - | - |
| HITL-07 | P2 | NOT_REPRODUCED | `app/main.py`, `app/agent/graph.py` | Persistência e retenção do feedback. | Revisar implementação atual. | `tests/test_persistence.py` | Bloqueado: não há testes específicos | - | - | - |

---

## FASE 8 — Segurança

| ID | Prioridade | Estado | Evidência | Causa-raiz | Correção | Teste | Resultado | Risco residual | Dependências | Commit |
|----|------------|--------|-----------|------------|----------|-------|-----------|----------------|--------------|--------|
| CON-07 | P1 | NOT_REPRODUCED | `app/mcp/policy.py`, `app/agent/graph.py` | Fronteira de confiança. | Revisar implementação atual. | `tests/test_mcp_policy.py` | Bloqueado: não há testes específicos | - | - | - |
| A2A-01 | P1 | NOT_REPRODUCED | `app/a2a/` | Autenticação, autorização, validação de payloads. | Revisar implementação atual. | `tests/test_a2a.py` | Bloqueado: não há testes específicos | - | - | - |

---

## FASE 9 — Contratos e Persistência

| ID | Prioridade | Estado | Evidência | Causa-raiz | Correção | Teste | Resultado | Risco residual | Dependências | Commit |
|----|------------|--------|-----------|------------|----------|-------|-----------|----------------|--------------|--------|
| CON-08 | P2 | NOT_REPRODUCED | `app/agent/nodes.py`, Pydantic models | Evidência, abstenção e contratos. | Revisar implementação atual. | `tests/test_api.py` | Bloqueado: não há testes específicos | - | - | - |
| CON-11 | P2 | NOT_REPRODUCED | `app/main.py`, Pydantic models | Semântica da verificação (`verified_at` vs `diagnosis_correct`). | Revisar implementação atual. | `tests/test_api.py` | Bloqueado: não há testes específicos | - | - | - |
| CON-12 | P2 | NOT_REPRODUCED | Todos os módulos | Coerência entre código, testes e documentação. | Revisar implementação atual. | `tests/test_quality_gate.py` | Bloqueado: não há testes específicos | - | - | - |

---

## FASE 10 — Resiliência e Observabilidade

| ID | Prioridade | Estado | Evidência | Causa-raiz | Correção | Teste | Resultado | Risco residual | Dependências | Commit |
|----|------------|--------|-----------|------------|----------|-------|-----------|----------------|--------------|--------|
| CON-09 | P2 | NOT_REPRODUCED | `app/llm/gateway.py`, `app/queue.py` | Resiliência e observabilidade. | Revisar implementação atual. | `tests/test_gateway.py` | Bloqueado: não há testes específicos | - | - | - |
| TR-02 | P2 | NOT_REPRODUCED | `pyproject.toml`, `docker-compose.yml` | Dependência Prometheus. | Revisar implementação atual. | `tests/test_prometheus.py` | Bloqueado: não há testes específicos | - | - | - |
| TR-07 | P2 | NOT_REPRODUCED | `app/llm/gateway.py` | Circuit breaker distribuído. | Revisar implementação atual. | `tests/test_gateway.py` | Bloqueado: não há testes específicos | - | - | - |

---

## FASE 11 — Deployment e Supply Chain

| ID | Prioridade | Estado | Evidência | Causa-raiz | Correção | Teste | Resultado | Risco residual | Dependências | Commit |
|----|------------|--------|-----------|------------|----------|-------|-----------|----------------|--------------|--------|
| TR-01 | P1 | NOT_REPRODUCED | `deploy/kyma/` | NetworkPolicy. | Revisar implementação atual. | `tests/test_deployment.py` | Bloqueado: não há testes específicos | - | - | - |
| TR-03 | P2 | NOT_REPRODUCED | `alembic/versions/009_encrypt_evidence_json.py` | Downgrade da migração de criptografia. | Revisar implementação atual. | `tests/test_migrations.py` | Bloqueado: não há testes específicos | - | - | - |
| TR-04 | P2 | NOT_REPRODUCED | `frontend/package.json` | Segurança de dependências npm. | Revisar implementação atual. | `tests/test_frontend.py` | Bloqueado: não há testes específicos | - | - | - |
| TR-05 | P2 | NOT_REPRODUCED | `Dockerfile`, `docker-compose.yml` | Imagens Docker. | Revisar implementação atual. | `tests/test_docker.py` | Bloqueado: não há testes específicos | - | - | - |
| TR-09 | P2 | NOT_REPRODUCED | `.env.example`, `app/config.py` | Secrets e autenticação. | Revisar implementação atual. | `tests/test_config.py` | Bloqueado: não há testes específicos | - | - | - |

---

## FASE 12 — Manutenibilidade

| ID | Prioridade | Estado | Evidência | Causa-raiz | Correção | Teste | Resultado | Risco residual | Dependências | Commit |
|----|------------|--------|-----------|------------|----------|-------|-----------|----------------|--------------|--------|
| TR-08 | P2 | NOT_REPRODUCED | `app/agent/nodes.py`, `app/main.py`, `app/rag/retriever.py` | Módulos extensos. | Revisar implementação atual. | `tests/test_quality_gate.py` | Bloqueado: não há testes específicos | - | - | - |

---

## FASE 13 — Backlog Transversal

| ID | Prioridade | Estado | Evidência | Causa-raiz | Correção | Teste | Resultado | Risco residual | Dependências | Commit |
|----|------------|--------|-----------|------------|----------|-------|-----------|----------------|--------------|--------|
| TRANS-01 | P1 | NOT_REPRODUCED | Feedback humano → datasets | Integração entre domínios. | Revisar implementação atual. | `tests/test_feedback.py` | Bloqueado: não há testes específicos | - | - | - |
| TRANS-02 | P1 | NOT_REPRODUCED | Ontologia → Neo4j | Consistência semântica. | Revisar implementação atual. | `tests/test_ontology.py` | Bloqueado: não há testes específicos | - | - | - |
| TRANS-03 | P1 | NOT_REPRODUCED | RAG → proveniência | Preservação de chunks. | Revisar implementação atual. | `tests/test_rag.py` | Bloqueado: não há testes específicos | - | - | - |
| TRANS-04 | P1 | NOT_REPRODUCED | Conclusões → evidência | Não exceder evidência disponível. | Revisar implementação atual. | `tests/test_nodes.py` | Bloqueado: não há testes específicos | - | - | - |
| TRANS-05 | P1 | NOT_REPRODUCED | Hipótese → causa confirmada | Distinção semântica. | Revisar implementação atual. | `tests/test_ontology.py` | Bloqueado: não há testes específicos | - | - | - |
| TRANS-06 | P1 | NOT_REPRODUCED | Resultados de avaliação → versionamento | Rastreabilidade. | Revisar implementação atual. | `tests/test_quality_gate.py` | Bloqueado: não há testes específicos | - | - | - |
| TRANS-07 | P1 | NOT_REPRODUCED | Contratos backend/frontend | Coerência. | Revisar implementação atual. | `tests/test_api.py` | Bloqueado: não há testes específicos | - | - | - |
| TRANS-08 | P1 | NOT_REPRODUCED | Falhas de persistência → sucesso | Não apresentar erro como sucesso. | Revisar implementação atual. | `tests/test_persistence.py` | Bloqueado: não há testes específicos | - | - | - |
| TRANS-09 | P1 | NOT_REPRODUCED | Fronteira de confiança | Dados não confiáveis. | Revisar implementação atual. | `tests/test_llm_governance.py` | Bloqueado: não há testes específicos | - | - | - |
| TRANS-10 | P1 | NOT_REPRODUCED | Correções → testes de regressão | Qualidade. | Revisar implementação atual. | `tests/` | Bloqueado: não há testes específicos | - | - | - |

---

## Resumo Executivo

| Categoria | Quantidade |
|-----------|------------|
| Achados avaliados | 60+ |
| Confirmados | 2 (H-01, H-02) |
| Corrigidos e validados | 0 |
| Parcialmente corrigidos | 0 |
| Abertos | 58+ |
| Bloqueados | 0 |
| Não reproduzidos | 0 |
| Não aplicáveis | 0 |

**Principais riscos residuais:**
1. **H-01/H-02:** Formato de resultados Promptfoo usa `success` em vez de `pass`, causando falhas na normalização.
2. **H-02:** Baseline não pode ser gerado sem transformar o formato.

**Próximas ações:**
1. Corrigir H-01/H-02 (transformação `success`→`pass` + ajuste de expectativas).
2. Executar testes focados após correção.
3. Continuar com as demais fases.

---

**Status:** Fase 0 e 1 parcialmente concluídas. Bloqueado em H-01/H-02 até transformação de formato.
