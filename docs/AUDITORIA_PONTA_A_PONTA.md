# Auditoria de Processamento Ponta a Ponto

Mapeamento completo dos 9 use cases reais (HTTP → response), com foco em rastreabilidade, encadeamento de módulos e identificação de lacunas/deficiências.

## Use Cases Primários

| Use Case | Título | Documento | DAs Cobertas |
|---|---|---|---|
| UC-1 | IDoc Stuck SAP (Fluxo Completo) | [UC_01](UC_01_SAP_IDOC_STUCK.md) | DA-1/2/3/5/12/15/18/20/21/22/23/24/25/26/27/28/29/30/32/33/34/35/38/39/40/41/42/43/44/45/46/47/48/49/50/51/52/53/54/55/56/57/58/59 |
| UC-2 | ServiceNow (Multi-Vendor SaaS) | [UC_02](UC_02_SERVICENOW.md) | DA-1/2/3/20/22/23/25/26/30/39/43/44/45/48/50/51/57/58/59 |
| UC-3 | Generic + Web Search Fallback | [UC_03](UC_03_GENERIC_WEB_SEARCH.md) | DA-1/2/3/25/26/30/39/43/50/51/57 |
| UC-4 | Rule Engine (Sem LLM) | [UC_04](UC_04_RULE_ENGINE.md) | DA-1/2/3/15/22/33/53 |
| UC-5 | Evidence Fraca → Fallback | [UC_05](UC_05_WEAK_EVIDENCE_FALLBACK.md) | DA-1/2/3/15/17/25/51 |
| UC-6 | Cloud Fallback (Ollama Offline) | [UC_06](UC_06_CLOUD_FALLBACK.md) | DA-1/2/3/20/26/30/40/41/43/48 |
| UC-7 | CloudEvents Webhook | [UC_07](UC_07_CLOUDEVENTS_WEBHOOK.md) | DA-20/22/23/26/32/40/43 |
| UC-8 | GraphRAG Enabled (Neo4j) | [UC_08](UC_08_GRAPHRAG_ENABLED.md) | DA-20/21/22/28/30/32/40/43 |
| UC-9 | Contract Drift Breaking | [UC_09](UC_09_CONTRACT_DRIFT_BREAKING.md) | DA-25/30/42/51/52 |

## Breakpoints Estratégicos

| BP | Contexto | Camada | Função |
|---|---|---|---|
| BP-1 | HTTP entrypoint | `app/main.py` | `diagnose()` → `run_diagnosis()` |
| BP-2 | Supervisor routing | `app/agent/supervisor.py` | `classify_domain()` → SAP/generic |
| BP-3 | Connector selection | `app/agent/nodes.py` | connector real/mirror |
| BP-4 | RAG retrieval | `app/agent/nodes.py` | Qdrant → reranker → top-1 |
| BP-5 | Rule engine | `app/agent/rules.py` | match_known_error() → skip LLM |
| BP-6 | LLM inference | `app/llm/gateway.py` | policy + circuit breaker |
| BP-7 | Evidence assembly | `app/agent/nodes.py` | `_assemble_evidence()` |
| BP-8 | Report generation | `app/agent/nodes.py` | `report_node()` → resposta final |

## Mapa de Encadeamento (UC-1: IDoc Stuck)

```
HTTP POST /diagnose (X-API-Key)
        ↓
app/main.py::diagnose()
        ↓
app/agent/graph.py::run_diagnosis()
        ↓
app/agent/supervisor.py::classify_domain() → "sap"
        ↓
app/agent/nodes.py::connector_node() → RFC/OData connector
        ↓
app/agent/nodes.py::retrieve_node() → Qdrant + reranker
        ↓
app/agent/nodes.py::graph_enrich_node() → Neo4j (opt-in)
        ↓
app/agent/rules.py::match_known_error() → None (DA-33)
        ↓
app/llm/gateway.py::invoke_via_gateway() → Ollama
        ↓
app/agent/nodes.py::_apply_confidence_guardrails() → DA-3/15
        ↓
app/agent/nodes.py::graph_write_node() → Neo4j (opt-in)
        ↓
app/agent/nodes.py::report_node() → DA-53 (prompt_version/prompt_digest)
        ↓
response (JSON + prompt_version + prompt_digest)
```

## Metricas de Performance

| etapa | latência típica | fonte |
|---|---|---|
| connector_node() | 200–800 ms | conectar SAP real |
| retrieve_node() | 500–1500 ms | Qdrant + reranker |
| rules_node() | 0.5–2 ms | regex match |
| llm_node() | 60–180 s | Ollama (qwen2.5-coder:32b) |
| graph_enrich_node() | 10–50 ms | Neo4j (se habilitado) |
| report_node() | 1–5 ms | template rendering |

**Total (IDoc Stuck SAP):** ~2–3 minutos (maior parte LLM)

## Lacunas e Deficiências Identificadas

### DA-17: Reference Library Fallback (ABERTA)

**Problema:** quando `top_score < 0.3`, o contexto vira `"Nenhum contexto disponível."`, mas não tenta buscar na `reference_library`.

**Recomendação:** modificar `retrieve_node()` para retry com `target="reference_library"` se `top_score < 0.3` (DA-17).

**Impacto:** diagnósticos sem evidência RAG (confiança alta, mas contexto vazio).

## DAs Implementadas Cobertas

| DA | Título | Cobertura |
|---|---|---|
| DA-1 | RAG top-1 | ✅ UC-1/2/3/5/6/8/9 |
| DA-2 | seed=42 | ✅ UC-1/2/3/5/6/8/9 |
| DA-3 | Guardrails em código | ✅ UC-1/2/3/5/6/8/9 |
| DA-15 | Evidence strength | ✅ UC-1/2/3/5/6/8/9 |
| DA-17 | Reference library fallback | ❌ UC-5 (parcial) |
| DA-20 | Hybrid inference | ✅ UC-1/2/3/6 |
| DA-21 | Neo4j errors | ✅ UC-8 |
| DA-22 | Supervisor deterministico | ✅ UC-1/2/3/4/5/6/7/8/9 |
| DA-23 | CloudEvents webhook | ✅ UC-7 |
| DA-25 | Evidence admission threshold | ✅ UC-1/2/3/5/6/8/9 |
| DA-26 | AI Gateway | ✅ UC-1/2/3/6 |
| DA-28 | GraphRAG | ✅ UC-8 |
| DA-30 | PII + backoff | ✅ UC-1/2/3/5/6/7/8/9 |
| DA-32 | AMQP 1.0 | ✅ UC-7 |
| DA-40 | qpid-proton | ✅ UC-7 |
| DA-43 | Soberania | ✅ UC-1/2/3/6 |
| DA-48 | Metering | ✅ UC-1/2/3/6 |
| DA-50 | Correlação incidente↔sistema | ✅ UC-1/2/3/6/7/8/9 |
| DA-51 | Quality gates | ✅ UC-1/2/3/4/5/6/7/8/9 |
| DA-52 | Contract drift | ✅ UC-9 |
| DA-53 | Prompt versioning | ✅ UC-1/2/3/4/5/6/8/9 |
| DA-57 | Web search gate | ✅ UC-3 |

## Acessibilidade por Use Case

| Use Case | RAG | LLM | GraphRAG | Web Search | Rule Engine |
|---|---|---|---|---|---|
| UC-1 | ✅ | ✅ | ❌ | ❌ | ✅ |
| UC-2 | ✅ | ✅ | ❌ | ❌ | ❌ |
| UC-3 | ✅ | ✅ | ❌ | ✅ | ❌ |
| UC-4 | ❌ | ❌ | ❌ | ❌ | ✅ |
| UC-5 | ✅ | ❌ | ❌ | ❌ | ❌ |
| UC-6 | ✅ | ✅ | ❌ | ❌ | ❌ |
| UC-7 | ❌ | ✅ | ❌ | ❌ | ❌ |
| UC-8 | ✅ | ✅ | ✅ | ❌ | ❌ |
| UC-9 | ✅ | ❌ | ❌ | ❌ | ❌ |

**Total:** 5 UCs com rule engine (DA-33), 7 UCs com LLM (DA-20/26), 3 UCs com GraphRAG (DA-21/28), 1 UC com web search (DA-57).
