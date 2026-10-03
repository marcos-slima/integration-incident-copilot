# Use Case 3: Generic (Web Search Fallback)

**Contexto:** Descrição textual genérica sem keyword SAP/SAAS → fallback para generic specialist + web search

## Fluxo (Generic Specialist)

### 1. Supervisor
- `interface_type` ausente ou desconhecido (`"custom_http"`)
- `description="API falhou após request timeout"` → não bate com `_SAP_KEYWORDS`
- `classify_domain()` → `agent_domain="generic"`

### 2. Connector
- `get_connector("custom_http")` → `HTTPConnector` (se Registro existir) ou `None` (fallback mock)
- Se connector mock → `is_mock=True`, `source_system="simulated"`

### 3. Retrieve
- Query textual (description)
- RAG: pode devolver hits baixos (score < 0.5)
- `evidence_strength` cálculo (DA-15):
  - Sem connector real + hits baixos → `evidence_strength = 0.0`

### 4. Web Search Gate (DA-57)
```python
# app/agent/nodes.py:430–473
def _web_search_allowed(state: CopilotState) -> bool:
    if not settings.web_search_enabled:
        return False
    if settings.web_search_policy == "disabled":
        return False
    # Verifica linha em web_search_sources
    source = resolve_approved_source(state.get("interface_type"))
    if not source:
        return False
    # Verifica top_score RAG < threshold
    top_score = state.get("retrieved_context", [{}])[0].get("rerank_score_calibrated", 0.0)
    return top_score < settings.web_search_threshold
```

### 5. Generic Diagnosis
- Persona: `_GENERIC_INTEGRATION_PERSONA` (agnóstico de fornecedor)
- Se `_web_search_allowed(state)` → `web_search_tool` disponível no agente ReAct
- Loop ReAct: decide buscar web se evidência fraca
- Fallback: `_fallback_diagnosis(raw)` se JSON parsing falhar

### 6. Evidence Bundle
- `system_observed`: mock connector (não confiável)
- `retrieved_document`: RAG hits (score baixo)
- `web_untrusted`: resultados web (DA-15: nível baixo)
- `user_reported`: description

## Exemplo de Estado

```python
state = {
    "interface_type": "custom_http",
    "description": "API request timeout after 30s",
    "retrieved_context": [{"source": "generic_timeout.md", "rerank_score_calibrated": 0.42}],
    "web_search_results": [],  # ainda não executada
}
```

### Web Search Activation

Se `web_search_enabled=true`, `resolve_approved_source("custom_http")` devolve linha (ex: `interface_type="custom_http"` com `site_filters=["stackoverflow.com"]`):

1. `top_score = 0.42 < 0.6` (threshold) → web search ativada
2. `_make_web_search_tool(state)` cria tool com `duckduckgo_search`
3. Agente ReAct consulta: `"API timeout custom_http stackoverflow"`
4. Resultado: top 3 URLs → embedding → rank → top hit: `stackoverflow.com/questions/...`
5. Grava `web_search_results` no state

## DA-57: Fail-Closed Sem Fallback

```python
# app/services/web_search_sources.py::resolve_approved_source()
if not settings.database_url:
    return None  # sem DB:Sem busca web
if not source_record:
    return None  # sem linha:Sem busca web
if not source_record.enabled:
    return None  # desabilitada:Sem busca web
return source_record
```

**Importante:** Nenhum dict fixo fallback — se linha ausente, `web_search_tool` não está disponível no LLM.
