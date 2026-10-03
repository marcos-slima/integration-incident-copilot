# Use Case 2: Incidente ServiceNow (Multi-Fornecedor SaaS)

**Contexto:** ServiceNowConnector recupera incidente de ticket com HTTP 403 (autorização falhou)

## Fluxo (Supervisor → Saas Specialist)

### 1. Supervisor
- `interface_type="servicenow"` → `_SAAS_INTERFACE_TYPES` → `agent_domain="saas"`
- DA-22: roteamento determinístico → `saas_diagnosis_node`

### 2. Connector
- `app/connectors/servicenow_connector.py` → `ServiceNowConnector`
- Fetch incident via `/api/now/table/incident?number=TICKET-123`
- Resultado: `status="error"`, `error_code="403"`, `message="User does not have permission"`

### 3. Retrieve
- Query: `description + connector.message`
- RAG contra `target="incidents"` (mesma base que SAP)
- Rerank score: 0.85 (top hit: servicenow_403_forbidden.md)

### 4. Saas Diagnosis
- Persona: `_ENTERPRISE_SPECIALIST_PERSONA` (multi-fornecedor SaaS)
- Rule engine check: hit em `auth_forbidden` (patterns: `403.*forbidden`)
- Se hit → rule engine early exit (sem LLM)
- Se miss → LLM com prompt enterprise

### 5. Report
- Evidence bundle:
  - Primary Evidence: `system_observed` (connector real + rule engine)
  - Supporting Facts: `retrieved_document` (RAG)

## Checklist DA-57 (8ª superfície)

| Superfície | Localização |
|---|---|
| Registry (Literals) | `app/models.py::IncidentRequest.interface_type` (Literal["servicenow", ...]) |
| Supervisor | `app/agent/supervisor.py::_SAAS_INTERFACE_TYPES` (linha 36) |
| CLI | `app/cli/diagnose.py` (choices para `--interface`) |
| UI (systems.html) | `<select name="connector_type">` (dropdown) |
| Web search seed | `alembic/versions/008_web_search_sources.py` (seed de `web_search_sources`) |
| Coverage map | `data/connector_coverage.yaml` (linha `servicenow:`) |
| Documentation | `docs/ARCHITECTURE.md` (coluna ServiceNow × OData/RFC/etc.) |
| Admin catalog | `app/admin/templates/systems.html` (cadastro de sistemas) |
