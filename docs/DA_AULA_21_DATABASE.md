# DA_AULA_21_DATABASE: Banco de Dados, Migrations e Modelos SQL

## Onde Este Documento Vive

- **Diretório:** `docs/DA_AULA_21_DATABASE.md`
- **Trilha:** Trilha de estudos do Integration Incident Copilot (trilha `fix/local-stack-and-eval-2026-09-26`)
- **Data de criação:** 2026-10-02
- **Modelo canônico:** `qwen3-coder-next:latest`

---

## 1. Objetivo

Documentar a camada de persistência do Integration Incident Copilot: migrations Alembic, ORM SQLAlchemy, modelos Pydantic e a relação entre dados estruturados (PostgreSQL) e não-estruturados (Qdrant/Neo4j).

**Públicos-alvo:**
- **COI (Customer Operations Intelligence):** entender SLA de incidentes, queries de acurácia
- **SOC (Security Operations Center):** rastreamento de PII, sensibilidade de dados
- **iPaaS (Integration Platform as a Service):** correlação sistema–incidente, drift de contrato

---

## 2. Arquitetura da Persistência

### 2.1 Stack

| Camada | Tecnologia | Onde |
|---|---|---|
| Engine async | `sqlalchemy.ext.asyncio` | `app/db.py` |
| Session factory sync | `sqlalchemy.orm.sessionmaker` | `app/db.py::get_sync_session_factory()` |
| Migrations | Alembic (PostgreSQL) | `alembic/versions/*.py` |
| ORM | SQLAlchemy 2.0 (DeclarativeBase) | `app/db.py::Base` |
| Data models | Pydantic v2 | `app/models.py` |
| DB opt-in | `DATABASE_URL` vazia = nenhuma conexão | `app/db.py::is_db_enabled()` |

### 2.2 Decisões de Arquitetura

| DA | Nome | Resumo da decisão |
|---|---|---|
| DA-01 | UUID primary key | Evita colisão em shards futuros; `gen_random_uuid()` como default |
| DA-02 | JSONB para evidence e error_codes | Queries analíticas sem tabelas auxiliares (ex: `WHERE evidence_json @> '{"type":"IDoc"}'`) |
| DA-03 | DateTime(timezone=True) | Timestamps UTC explícitos → dashboards multi-timezone corretos |
| DA-04 | Índices compostos | `agent_domain+created_at` (COI), `llm_provider+created_at` (iPaaS), `sensitivity_level+created_at` (SOC) |
| DA-05 | evidence_strength change String→Float | Migration 002 corrige coluna para permitir agregações e percentis no PostgreSQL |

### 2.3 Migrations Aplicadas (001–008)

| ID | Título | DA relacionadas | Tabelas criadas |
|---|---|---|---|
| 001 | `incidents` | DA-35 | `incidents` |
| 002 | `evidence_strength` type fix | DA-05 | — (ALTER TABLE) |
| 003 | LLM registry tables | DA-46/47/48 | `llm_models`, `llm_credentials`, `llm_usage` |
| 004 | Integration systems | DA-49 | `integration_systems` |
| 005 | System contracts | DA-52 | `system_contracts` |
| 006 | Prompt provenance | DA-53 | — (ADD COLUMN) |
| 007 | Web users | DA-55 | `web_users` |
| 008 | Web search sources | DA-57 | `web_search_sources` |

**Comando para verificar:**
```bash
uv run alembic current
# Saída esperada: head (008_create_web_search_sources)
```

---

## 3. Modelos Pydantic e SQL (Relação 1:1)

### 3.1 `incidents` vs `DiagnosisResponse`

| Campo Pydantic | Tipo Pydantic | Coluna SQL | Observação |
|---|---|---|---|
| `probable_root_cause` | `str` | `TEXT` | — |
| `model_confidence` | `float` | `FLOAT` | DA-05: era `String(32)` |
| `diagnosis_confidence` | `float` | — | **não persistido**, cálculo em memória |
| `evidence_strength` | `float` | `FLOAT` | DA-05: conversão migration 002 |
| `llm_provider_used` | `str \| None` | `VARCHAR(64)` | — |
| `agent_domain` | `str \| None` | `VARCHAR(64)` | — |
| `evidence` | `list[Evidence]` | `JSONB` | coluna `evidence_json` |
| `incident_id` | `str \| None` | `UUID` | referencia ao trace_id do Langfuse |

**Observação crítica (DA-05):** antes da migration 002, `evidence_strength` era `String(32)` e impedia queries agregadas. O padrão de fallback em string (ex: `'high'`) foi substituído por value range `0.0–1.0`.

### 3.2 `integration_systems` vs `SystemContract`

| Campo | Tipo SQL | Tipo Pydantic | Observação |
|---|---|---|---|
| `system_key` | `VARCHAR(64)` | — | Chave única do catálogo |
| `connector_type` | `VARCHAR(32)` | Literal do pipeline | `odata/rfc/servicenow/...` |
| `base_url` | `VARCHAR(256)` | `AnyUrl` (Pydantic) | — |

### 3.3 `system_contracts` (DA-52)

| Campo | Tipo | Observação |
|---|---|---|
| `contract` | `JSONB` | Contrato normalizado EDMX/$metadata |
| `fingerprint` | `VARCHAR(64)` | SHA-256 do contrato canônico |
| `observed_at` | `TIMESTAMPTZ` | Timestamp UTC (TimescaleDB compatível) |
| **FK para `integration_systems`** | — | **Nenhuma** → histórico append-only |

**Pattern:** `system_contracts` é **append-only**, sem FK para `integration_systems`. Isso permite observar contract drift mesmo para sistemas só definidos no `.env`.

---

## 4. Fixtures de Teste (PostgreSQL)

### 4.1 Inicialização

```bash
# Start Infra (Postgres)
docker compose --profile observability up -d postgres

# Verificar conectividade
psql postgresql://postgres:postgres@127.0.0.1:5432/iic -c "SELECT version();"

# Aplicar migrations
uv run alembic upgrade head
```

### 4.2 Seed de Dados (para testes de dashboard)

```sql
-- 1 incidente por dominio (COI)
INSERT INTO incidents (id, description, agent_domain, llm_provider_used, created_at, model_confidence, evidence_strength)
VALUES
  (gen_random_uuid(), 'IDoc stuck', 'sap', 'ollama', now() - '2 days'::interval, 0.92, 0.85),
  (gen_random_uuid(), 'RFC call timeout', 'sap', 'openai', now() - '3 days'::interval, 0.88, 0.72),
  (gen_random_uuid(), 'OData 401', 'sap', 'azure_openai', now() - '4 days'::interval, 0.95, 0.91);

-- 1 incidente por provider (iPaaS)
INSERT INTO incidents (id, description, llm_provider_used, created_at, sensitivity_level, pii_detected)
VALUES
  (gen_random_uuid(), 'OAuth2 token expired', 'openai', now() - '1 day'::interval, 'confidential', false),
  (gen_random_uuid(), 'API rate limit exceeded', 'azure_openai', now() - '2 days'::interval, 'internal', false);

-- 1 incidente por sensibilidade (SOC)
INSERT INTO incidents (id, description, sensitivity_level, created_at, pii_detected, redaction_applied)
VALUES
  (gen_random_uuid(), 'CPF exposto', 'secret', now() - '0.5 day'::interval, true, false);
```

---

## 5. Padrões de Uso

### 5.1 Query de SLA p95 (COI)

```sql
SELECT
  agent_domain,
  PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms
FROM incidents
WHERE created_at >= NOW() - INTERVAL '30 days'
GROUP BY agent_domain;
```

**Dashboards Grafana:**
- `app/admin/templates/dashboards/dashboard_coi.json` (query `sla_p95_query`)
- `scripts/validate_dashboards.py` (validação de queries SQL)

### 5.2 Query de Acurácia (COI)

```sql
SELECT
  COUNT(*) FILTER (WHERE verified_at IS NOT NULL) AS total_verified,
  COUNT(*) FILTER (WHERE verified_at IS NOT NULL AND diagnosis_correct = true) AS correct,
  ROUND(
    COUNT(*) FILTER (WHERE verified_at IS NOT NULL AND diagnosis_correct = true)::numeric *
    100 / NULLIF(COUNT(*) FILTER (WHERE verified_at IS NOT NULL), 0),
    2
  ) AS accuracy_pct
FROM incidents
WHERE created_at >= NOW() - INTERVAL '30 days';
```

### 5.3 Query de PII Não Redigita (SOC)

```sql
SELECT
  created_at,
  incident_id,
  sensitivity_level,
  pii_detected,
  redaction_applied
FROM incidents
WHERE pii_detected = true AND redaction_applied = false
ORDER BY created_at DESC
LIMIT 10;
```

---

## 6. Quality Gates (Verificação de Integridade)

### 6.1 Gates Aplicáveis

| Gate | Verifica | Referência |
|---|---|---|
| `implemented_das_documented` | Todas as migrations registradas no `README.md` | `scripts/quality_gate.py` |
| `docs_code_references` | Referências a arquivos existentes (`app/db.py`, `alembic/`) | `scripts/quality_gate.py` |
| `prompt_digest_measured` | Consistência entre `app/agent/prompts.py` e baseline | `scripts/quality_gate.py` |

### 6.2 Comandos de Validação

```bash
# Validação completa
uv run python scripts/quality_gate.py --strict

# Validação de queries SQL (Grafana vs Postgres real)
uv run python scripts/validate_dashboards.py
```

---

## 7. Exercícios Práticos

### 7.1 Start Infra e migrations

```bash
# Start Postgres
docker compose --profile observability up -d postgres

# Verificar conectividade
docker compose --profile observability exec postgres psql -U postgres -c "SELECT 1"

# Aplicar migrations
uv run alembic upgrade head

# Verificar migração atual
uv run alembic current
```

### 7.2 Executar Query Simples

```python
# app/db.py: get_sync_session_factory()
from app.db import get_sync_session_factory
from sqlalchemy import text

factory = get_sync_session_factory()
with factory() as session:
    result = session.execute(text("SELECT COUNT(*) FROM incidents"))
    count = result.scalar()
    print(f"Total incidents: {count}")
```

### 7.3 Adicionar Coluna via Migration

```python
# alembic/versions/009_add_custom_field.py
revision = "009"
down_revision = "008"

def upgrade() -> None:
    op.add_column("incidents", sa.Column("custom_field", sa.String(128), nullable=True))

def downgrade() -> None:
    op.drop_column("incidents", "custom_field")
```

**Aplicar:**
```bash
uv run alembic revision -m "add custom_field to incidents"
# Editar o arquivo gerado
uv run alembic upgrade head
```

### 7.4 Validar Rollback

```bash
# Reverter última migration
uv run alembic downgrade -1

# Aplicar novamente
uv run alembic upgrade head
```

### 7.5 Consultar migrations aplicadas (SQL)

```sql
SELECT version_num, owner, description
FROM alembic_version
JOIN (
  SELECT 'da_46_47_48' AS version_num, 'DA-46/47/48' AS description UNION ALL
  SELECT 'da_49', 'DA-49' UNION ALL
  SELECT 'da_52', 'DA-52' UNION ALL
  SELECT 'da_53', 'DA-53' UNION ALL
  SELECT 'da_55', 'DA-55' UNION ALL
  SELECT 'da_57', 'DA-57'
) da ON version_num LIKE '%' || replace(da.version_num, 'da_', '') || '%';
```

### 7.6 (Opcional) Custom Dashboard JSON

```bash
# Exportar dashboard do Grafana
curl -H "Authorization: Bearer ${GRAFANA_API_KEY}" \
  "http://localhost:3001/api/dashboards/Uid/iic-critical-alerts" \
  | jq '.dashboard | del(.id, .uid, .version)' > custom_dashboard.json

# Importar para outro ambiente
curl -X POST -H "Authorization: Bearer ${GRAFANA_API_KEY}" \
  -H "Content-Type: application/json" \
  -d @custom_dashboard.json \
  "http://localhost:3001/api/dashboards/db"
```

---

## 8. Invariantes (Nunca Alterar Sem DA)

| # | Invariante | Motivação | Fonte |
|---|---|---|---|
| 1 | `DATABASE_URL` vazia = nenhuma conexão | `app/db.py:128-146`: nenhuma conexão é aberta se `settings.database_url` for vazia | app/db.py:128 |
| 2 | `sync_factory` cacheado por URL | `app/db.py:76-107`: cache de `(url, sessionmaker)` para evitar recriação em cada chamada | app/db.py:76 |
| 3 | `system_contracts` append-only, sem FK | `alembic/versions/005_create_system_contracts.py:12-22`: histórico de observações sem FK estático | alembic/005 |
| 4 | `evidence_strength` FLOAT, Nunca `String(32)` | Migration 002: coluna convertida para permitir agregações no PostgreSQL | app/models.py:208-217 |
| 5 | `llm_model`, `prompt_version`, `prompt_digest` NULLáveis | Migration 006: rule engine não usa prompt → NULL é resposta correta | alembic/006 |
| 6 | `verified_at` não coagido para `True` | DA-50: `diagnosis_correct=None` quando não verificado; coagir infla acurácia | app/models.py:282-300 |

---

## 9. Limitações (Conhecidas, Aceitas)

| Limitação | Impacto | Alternativa futura |
|---|---|---|
| Sem cache de `sessionmaker` cross-process | Cada container recria pool | Redis connection pool |
| Sem backup automático (`TIMESCALEDB` opt-in) | Requisito manual de snapshot | `SELECT create_hypertable('incidents', 'created_at')` |
| `web_search_sources` sem FK para `integration_systems` | Linhas órfãs possíveis | Validation layer no admin |
| Migration `downgrade` raramente testada | Risco de regressão em rollback | CI/CD com `alembic downgrade head -1 upgrade head` |

---

## 10. Referências

| Arquivo | DA | O que é |
|---|---|---|
| `app/db.py` | — | Engine SQLAlchemy async + session factory sync |
| `alembic/versions/001_create_incidents_table.py` | DA-35 | Tabela `incidents` com UUID, JSONB, índices compostos |
| `alembic/versions/002_fix_evidence_strength_type.py` | DA-05 | Migration String→Float para agregações |
| `alembic/versions/003_create_admin_tables.py` | DA-46/47/48 | LLM registry, credentials, metering |
| `alembic/versions/004_create_integration_systems.py` | DA-49 | Catálogo de sistemas integrados |
| `alembic/versions/005_create_system_contracts.py` | DA-52 | Baseline de contrato (append-only) |
| `alembic/versions/006_incident_prompt_provenance.py` | DA-53 | Proveniencia de prompt e modelo |
| `alembic/versions/007_create_web_users.py` | DA-55 | Usuários da UI web |
| `alembic/versions/008_create_web_search_sources.py` | DA-57 | Fontes de busca web aprovadas |
| `app/models.py` | — | Pydantic models (DiagnosisResponse, IncidentRequest, etc.) |
| `scripts/validate_dashboards.py` | DA-35 | Valida queries SQL (45 queries) |
| `docs/TROUBLESHOOTING.md` | — | Troubleshooting PostgreSQL (deadlocks, connection pool) |

---

## Decisões de Arquitetura Registadas

| DA | Título | Resumo |
|---|---|---|
| DA-01 | UUID primary key | Evita colisão em shards futuros; `gen_random_uuid()` default |
| DA-05 | evidence_strength Float | Migration 002 corrige `String(32)` → `FLOAT` para agregações |
| DA-20 | Hybrid Inference | Ollama local → cloud fallback (DA-20) |
| DA-25 | Evidence/Trust Layer | `evidence_strength` FLOAT, trust_level determinístico |
| DA-35 | Health probe real | `/health` GET nos serviços (Postgres, Qdrant, Ollama) |
| DA-46/47/48 | LLM registry + metering | Registro gerenciado de modelos/credenciais, tokens reais persistidos |
| DA-49 | Catalogo de sistemas integrados | `integration_systems` com `connector_type` Literal |
| DA-52 | Drift de contrato SAP | Baseline append-only (`system_contracts`) com fingerprint |
| DA-53 | Prompt provenance | `llm_model`, `prompt_version`, `prompt_digest` em `incidents` |
| DA-55 | Usuários web | `web_users` com ativação em duas etapas (token e-mail + código telefone) |
| DA-57 | Web search sources | Tabela `web_search_sources` substitute mapas literals (DA-57) |

---

## Como Validar Este Documento

```bash
# 1. Quality gate
uv run python scripts/quality_gate.py --strict

# 2. Queries SQL validadas
uv run python scripts/validate_dashboards.py

# 3. Migrations aplicadas
uv run alembic current
```

**Validação esperada:**
- `quality_gate`: 17/17 PASS, 0 falhas
- `validate_dashboards.py`: 45/45 queries OK
- `alembic current`: `head (008_create_web_search_sources)`

---

**Status:** Concluído (2026-10-02)
**Próxima Aula:** DA_AULA_22_MONITORING.md (Alerting & Dashboards)
**Relacionados:** DA-01, DA-05, DA-20, DA-25, DA-35, DA-46/47/48, DA-49, DA-50, DA-52, DA-53, DA-55, DA-57
