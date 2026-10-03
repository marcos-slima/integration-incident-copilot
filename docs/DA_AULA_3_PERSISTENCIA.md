# Aula 3 — Persistence (PostgreSQL, Redis, Migrations)

**Objetivo:** entender como o projeto implementa persistência Assíncrona (PostgreSQL), fila (Redis RQ) e migrations (Alembic), incluindo tabela `incidents`,Redis para idempotência distribuída e migrations para schema.

## Contexto

O projeto usa três tecnologias principais de persistência (todas **opt-in**, controladas por variáveis de ambiente):

| Tecnologia | Propósito | Variável de configuração |
|------------|-----------|--------------------------|
| PostgreSQL (asyncpg) | Tabelas SQL (`incidents`, `llm_models`, `integration_systems`, etc.) | `DATABASE_URL` |
| Redis | Fila RQ (`/diagnose/async`), sessões A2A, idempotência distribuída | `REDIS_URL` |
| Neo4j (GraphRAG) | GRAFO de evidências e correlações (DA-28) | `NEO4J_URI` (opt-in via `GRAPH_RAG_ENABLED=true`) |

**Pattern principal:** **fallback em memória** quando infra está ausente — o app sobe mesmo sem banco (útil para desenvolvimento local ou testes unitários sem infra estruturada).

---

## PostgreSQL — Engine Async com Alembic

### Arquivo principal: `app/db.py`

#### Dialeto async nativo

```python
# URL normalização (DA-52)
def _async_url(raw: str) -> str:
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+asyncpg://", 1)
    return raw  # já usa asyncpg
```

- Aceita `postgresql://`, `postgres://` ou `postgresql+asyncpg://`
- Converte para `asyncpg` transparentemente
- Mesma URL serve para sync (`psycopg2`) e async (`asyncpg`)

#### Session factory sync/async compartilhada

```python
# Async (fastapi endpoints)
engine = create_async_engine(_async_url(url), pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# Sync (worker RQ, incident_recorder, alembic)
def get_sync_session_factory() -> sessionmaker | None:
    # Cache keyed by URL (evita lru_cache sem chave travando None)
    # Retorna None se DATABASE_URL vazia
```

**Por que duas factories?**
- FastAPI endpoints rodam async (`asyncio`)
- Worker RQ e migrations precisam sync (`psycopg2`)
- `pool_pre_ping=True` reativa conexões mortas antes de usar (Heroku/CloudScale)

#### Lifespan de inicialização

```python
# app/main.py (lifespan)
async def lifespan(app: FastAPI):
    if settings.database_url:
        engine = create_async_engine(_async_url(settings.database_url), pool_pre_ping=True)
        app.state.db_engine = engine
        # Valida schema (new: create_all não é idempotente para schema changes)
        # Em produção: `alembic upgrade head` antes do deploy
    yield
```

- Cria `engine` se `DATABASE_URL` está definida
- Faz nothing se vazia (fallback em memória)
- Validar schema com `alembic upgrade head` antes do `uvicorn`

---

## Migration System — Alembic

### Arquivo principal: `alembic/env.py`

```python
# alembic.ini
target_metadata = Base.metadata  # SQLAlchemy Base declarativa

# env.py
def run_migrations_online():
    engine = create_engine(database_url, ... )
    with engine.connect() as connection:
        context.configure(connection=connection, ...)
        context.run_migrations()
```

#### Estrutura de migrations

| ID | Nome | Tabela(s) | Objetivo |
|----|------|-----------|----------|
| 001 | create_incidents_table | `incidents` | Schema inicial (DA-25: evidence_strength inicialmente `String(32)`) |
| 002 | fix_evidence_strength_type | `incidents` | Corrige `evidence_strength` de `String(32)` para `Float` (DA-25: campo é `float`) |
| 003 | create_admin_tables | `llm_models`, `llm_credentials`, `llm_usage` | Registro de modelos (DA-46), credenciais cifradas (DA-47), metering (DA-48) |
| 004 | create_integration_systems | `integration_systems` | Catalogo de sistemas integrados (DA-49) |
| 005 | create_system_contracts | `system_contracts` | Baseline de contrato SAP (DA-52: observed_at, fingerprint) |
| 006 | incident_prompt_provenance | `incidents` | Adiciona `llm_model`, `prompt_version`, `prompt_digest` (DA-53) |
| 007 | create_web_users | `web_users` | Usuários da UI web (DA-55: status pending_email → pending_phone → active) |
| 008 | create_web_search_sources | `web_search_sources` | Fontes de busca web por `interface_type` (DA-57: substitui mapas hardcoded) |

### Princípios de migrations

1. **Opt-in:** Tabelas existem **só** se `DATABASE_URL` configurada e `alembic upgrade head` executado
2. **Compatibilidade:** Todos os schemas usam PostgreSQL + SQLite (dialeto async `aiosqlite` para testes)
3. **No FK:** Exceções: `llm_credentials.primary_key=provider_origin` (chave estrangeira não existe)
4. **Append-only:** `system_contracts` não tem FK para `integration_systems` (basta observar sistema externo ao catalogo)

#### Exemplo: Migration 006 (DA-53)

```python
def upgrade() -> None:
    op.add_column("incidents", sa.Column("llm_model", sa.String(length=128), nullable=True))
    op.add_column("incidents", sa.Column("prompt_version", sa.String(length=32), nullable=True))
    op.add_column("incidents", sa.Column("prompt_digest", sa.String(length=64), nullable=True))
```

- `nullable=True`: diagnostico rule-engine (DA-33) sem prompt não tem proveniência
- Default `'desconhecido'` **evitado**: geraria procedência falsa
- NULL = "não sei", resposta correta para dados pre-migration

---

## Redis — Fila RQ e Idempotência

### Arquivo principal: `app/queue.py` e `app/events/idempotency.py`

#### Redis RQ para fila assíncrona

```python
def run_diagnosis_job(request_data: dict[str, Any]) -> dict[str, Any]:
    """Função executada pelo worker RQ (processo separado)."""
    from app.agent.graph import run_diagnosis
    from app.models import IncidentRequest
    request = IncidentRequest(**request_data)
    return run_diagnosis(request).model_dump()
```

**Use:**
```bash
# Worker (processo separado)
uv run python -m app.queue  # inicia worker

# Endpoint assíncrono
POST /diagnose/async
```

**Pattern de fallback:**
- Sem `REDIS_URL` configurada → `POST /diagnose/async` devolve HTTP 503 (`AsyncQueueUnavailableError`)
- Sem infra → `/diagnose` continua sincrono (não quebra behavior)

#### Idempotência distribuída

**Problema:** `events:seen:<id>` em memoria falha com Kyma replicas > 1 (cada pod tem seu set)

**Solução:** Redis SET NX EX por evento:

```python
def is_duplicate(event_id: str) -> bool:
    key = f"events:seen:{event_id}"
    # SET NX EX: claim + TTL atomico
    result = redis_client.set(key, _PROCESSING, nx=True, ex=lease_seconds)
    return not result
```

**Ciclo de vida:**
1. `is_duplicate(event_id)` → claim com TTL `lease_seconds`
2. Processamento bem-sucedido → `mark_completed(event_id)` (TTL 24h → "done")
3. Falha observada → `release(event_id)` (apaga chave, reentrega aceita)

**Fallback em memória (LRU):**
- Se `REDIS_URL` vazia → LRU de 2000 IDs
- Log warning: "Redis missing, using local fallback"
- Dedup dentro de um único pod ( comportamento pre-P1.1)

---

## Tabelas Admin

### Arquivo principal: `app/admin/models.py`

Todas opt-in (só existem com `DATABASE_URL` + migrations):

| Tabela | DA | Finalidade | Objetivo |
|--------|----|------------|----------|
| `llm_models` | DA-46 | Catalogo de modelos por ORIGEM | `provider_origin` (ex: `local_lab`, `api.openai.com`) + `model_id` |
| `llm_credentials` | DA-47 | Credenciais cifradas com Fernet | `provider_origin` como PK + `encrypted_key` |
| `llm_usage` | DA-48 | Metering de tokens reais | `period_start`/`period_end` (aberto = `period_end IS NULL`) |
| `integration_systems` | DA-49 | Catalogo de sistemas integrados | `system_key`, `connector_type`, `base_url`, `status` |
| `system_contracts` | DA-52 | Baseline de contrato SAP | `observed_at` (append-only), `fingerprint`, JSONB |
| `web_users` | DA-55 | Usuários da UI web | `status`: pending_email → pending_phone → active |
| `web_search_sources` | DA-57 | Fontes de busca web aprovadas | `interface_type` unique + `site_filter` + `tech_term` |

#### Exemplo: `llm_usage` (DA-48)

```python
class LlmUsage(Base):
    __tablename__ = "llm_usage"

    id = Column(Uuid, primary_key=True)
    provider_origin = Column(String(128), nullable=False, index=True)
    model_id = Column(String(128), nullable=False)
    period_start = Column(DateTime(timezone=True), nullable=False)
    period_end = Column(DateTime(timezone=True), nullable=True)  # NULL = periodo aberto
    tokens_in = Column(BigInteger, nullable=False, default=0)
    tokens_out = Column(BigInteger, nullable=False, default=0)
    requests = Column(Integer, nullable=False, default=0)
```

**Princípios:**
- Um periodo aberto por `(provider_origin, model_id)` ( índice parcial `period_end IS NULL` )
- Percentual consumido calculado por query, não armazenado
- Best-effort: falha de DB logada e ignorada (diagnostico não quebra)

---

## Tabela `incidents`

### Arquivo principal: `app/services/incident_recorder.py`

```python
def record_incident(**kwargs: Any) -> None:
    """Persiste o diagnostico (mesmos argumentos de build_incident_row).
    No-op sem DATABASE_URL; qualquer erro e logado e engolido."""
    if not settings.database_url:
        return  # No-op: diagnostico continua funcionando

    try:
        from app.services.incident_repository import Incident
        row = build_incident_row(**kwargs)
        with _get_session_factory()() as session:
            session.add(Incident(**row))
            session.commit()
    except Exception:
        _logger.exception("[incidents] Falha ao gravar incidente %s no PostgreSQL (diagnostico nao afetado)", ...)
        return  # Best-effort: diagnostico não pode quebrar
```

#### Colunas principais

| Coluna | Tipo | DA | Nota |
|--------|------|----|------|
| `id` | `UUID` | 001 | `gen_random_uuid()` (ex: `550e8400-e29b-41d4-a716-446655440000`) |
| `trace_id` | `String(128)` | - | Correlação com Langfuse/OpenTelemetry |
| `interface_type` | `String(64)` | 001 | Conector (odata, rfc, servicenow, ...) |
| `description` | `Text` | 001 |Redigida com PII redacted (DA-30) |
| `connector_source_system` | `String(128)` | 001 | DA-50: system_key优先于 connector_type fallback |
| `evidence_strength` | `Float` | 002 | Corrigido de `String(32)` (DA-25) |
| `verified_at` | `DateTime(timezone=True)` | 001 | Quando operador verifica (NULL = nunca) |
| `diagnosis_correct` | `Boolean` | 001 | Veredito (None = sem veredito) |
| `llm_model` | `String(128)` | 006 | DA-53: nome real (ex: `qwen3-coder-next:latest`) |
| `prompt_digest` | `String(64)` | 006 | DA-53: SHA256 do prompt artefato |

#### Idempotência no endpoint `/incidents/{id}/verify`

```python
def record_verification(
    incident_id: str,
    *,
    diagnosis_correct: bool | None,
    verified_by: str | None = None,
    verified_root_cause: str | None = None,
) -> bool:
    """DA-50: grava a verificacao humana na tabela `incidents` (best-effort)."""
    if not settings.database_url:
        return False  # No-op: verificacao no grafo/Langfuse continua funcionando

    try:
        with _get_session_factory()() as session:
            result = session.execute(
                update(Incident)
                .where(Incident.id == uuid.UUID(incident_id))
                .values(
                    verified_at=datetime.now(UTC),
                    diagnosis_correct=diagnosis_correct,  # None = sem veredito
                    verified_by=verified_by,
                    verified_root_cause=verified_root_cause,
                )
            )
            session.commit()
        return result.rowcount > 0
    except Exception:
        _logger.exception("[incidents] Falha ao gravar verificacao do incidente %s", incident_id)
        return False  # Best-effort
```

**Invariante DA-50:** `diagnosis_correct=None` significa "verificado sem veredito" → grava `verified_at` deixando `diagnosis_correct` NULL (coagir para True inflaria acurácia nos dashboards).

---

## Fluxo Completo: Incidente para Persistência

### 1. Endpoint `/diagnose`

```python
# app/main.py
@router.post("/diagnose")
async def diagnose(request: IncidentRequest):
    response = run_diagnosis(request)
    record_incident(
        incident_id=response.id,
        request=request,
        response=response,
        final_state=state.model_dump(),
        latency_ms=latency_ms,
    )
    return response
```

### 2. Worker RQ

```python
# app/queue.py
def run_event_job(envelope_data: dict[str, Any]) -> dict[str, Any]:
    from app.events import idempotency
    envelope = IncidentEventEnvelope(**envelope_data)
    if idempotency.is_duplicate(envelope.id):
        return {"status": "duplicate", "cloudevents_id": envelope.id}

    result = run_diagnosis(to_incident_request(envelope)).model_dump()
    idempotency.mark_completed(envelope.id)
    return result
```

### 3. webhook AMQP / Event Mesh

```python
# app/events/amqp_consumer.py
async def handle_message(body: bytes):
    envelope = IncidentEventEnvelope.model_validate_json(body)
    if not idempotency.is_duplicate(envelope.id):
        try:
            result = await run_diagnosis_async(to_incident_request(envelope))
            idempotency.mark_completed(envelope.id)
            await channel.basic_ack(delivery_tag=delivery_tag)
        except Exception:
            idempotency.release(envelope.id)
            await channel.basic_nack(delivery_tag=delivery_tag, requeue=True)
```

---

## Comandos Úteis

### Migrations

```bash
# Gerar nova migration
uv run alembic revision -m "description" --autogenerate

# Aplicar todas as migrations
uv run alembic upgrade head

# Reverter última migration
uv run alembic downgrade -1
```

### Redis (CLI)

```bash
# Ver chaves events:seen
redis-cli keys "events:seen:*"

# Ver TTL de uma chave
redis-cli TTL "events:seen:<id>"

# Apagar uma chave (release manual)
redis-cli DEL "events:seen:<id>"
```

### PostgreSQL (CLI)

```bash
# Ver tabelas
psql $DATABASE_URL -c "\dt"

# Ver colunas de uma tabela
psql $DATABASE_URL -c "\d incidents"

# Consultar incidentes recentes
psql $DATABASE_URL -c "SELECT id, interface_type, evidence_strength, verified_at FROM incidents ORDER BY created_at DESC LIMIT 10"
```

---

## Invariantes e Regras Críticas

### Invariante 1: Fallback em memória

> Se `DATABASE_URL` vazia → app sobe normalmente, **sem** exception. Tabelas Admin e `incidents` são No-Op.

**Raciocínio:** "clone e rode" para desenvolvedores — não exigir infra estruturada antes de mostrar valor.

### Invariante 2: Best-effort

> Falha de DB logada e engolida. Diagnostico **nunca** quebra por falta de banco.

**Raciocínio:** Diagnóstico é o produto principal; persistência é analítica (Analytics/Reporting/Grafana).

### Invariante 3: Idempotência

> `events:seen:<id>` com SET NX EX (claim + TTL atomico). TTL de lease cobre watchdog do diagnostico.

**Raciocínio:** Kyma replicas > 1 exigem dedup distribuído (cada pod tem seu próprio set em memória).

### Invariante 4: Append-only

> `system_contracts` nunca apaga/Atualiza (só novo insertion com `observed_at > max(observed_at)`). Senão perde baseline histórico.

**Raciocínio:** Baseline e histórico de observações são o produto do detector de drift (DA-52).

### Invariante 5: NULL ≠ default

> `diagnosis_correct=None` grava NULL (não coagir para `True`). `llm_model`/`prompt_digest` NULL se rule engine.

**Raciocínio:** Default `'desconhecido'` fabricaria procedência falsa. NULL = "não sei", resposta correta.

---

## Próximos Passos

- [ ] **Módulo 6:** Monitoring + Observability (Langfuse, Grafana, Prometheus, dashboards)
- [ ] **Módulo 7:** RAG (Qdrant, embedders, rerankers, hybrid检索)
- [ ] **Módulo 8:** Agent2Agent (JSON-RPC 2.0, `app/a2a/`)
- [ ] **Módulo 9:** MCP (StreamableHTTP, capability catalog, `app/mcp/`)

---

## Referências

| Referência | Arquivo | Nota |
|------------|---------|------|
| DA-25 | `app/models.py`, `alembic/002`, `app/evaluation/gates.py` | `evidence_strength` é `Float` (DA-22 define threshold RAG pós-reranker) |
| DA-30 | `app/redaction.py` | PII redaction antes de gravar no banco |
| DA-43 | `app/llm/gateway.py` | Soberania de dados por origin real, fail-closed |
| DA-45 | `app/llm/routes.py` | `provider_origin` (não `llm_model`) é a chave de rota |
| DA-46/47/48 | `app/admin/models.py`, `app/admin/repository.py`, `app/llm/gateway.py` | Registro de modelos, credenciais cifradas, metering real |
| DA-49 | `app/admin/models.py`, `app/admin/repository.py`, `app/admin/routes.py`, `alembic/004` | Catalogo de sistemas integrados |
| DA-50 | `app/admin/correlation.py`, `app/agent/graph.py`, `app/services/incident_recorder.py::record_verification` | Correlação incident ↔ system (fail-closed por ambiguidade) |
| DA-52 | `app/contracts/`, `alembic/005`, `app/db.py::get_sync_session_factory()` | Baseline append-only, sync factory compartilhada |
| DA-53 | `alembic/006`, `app/agent/prompts.py`, `app/evaluation/gates.py::check_prompt_digest` | Proveniencia de prompt (digest SHA256 do artefato) |
| DA-55 | `app/webusers.py`, `app/auth.py`, `alembic/007` | Usuários da UI: token email → code phone (out-of-band) |
| DA-57 | `app/admin/models.py::WebSearchSource`, `app/agent/nodes.py::_web_search_allowed`, `alembic/008` | Fontes de busca web por `interface_type`, fail-closed |
| DA-58 | `data/sap_products.yaml`, `app/evaluation/coverage.py`, `scripts/coverage_map.py` | Mapa de cobertura produto × mecanismo (dedicated/generic/absent) |
| DA-59 | `app/connectors/__init__.py`, `app/models.py`, `app/agent/supervisor.py`, `docs/CONNECTORS.md` | Conectores multi-vendor: fluxo `fetch(identifier) → ConnectorResult` |

---

**Próxima aula:** Módulo 6 — Monitoring + Observability (Langfuse, Grafana, dashboards SQL, Prometheus metrics).
