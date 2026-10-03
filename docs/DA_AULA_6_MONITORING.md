# Aula 6 — Monitoring & Observability (Langfuse, Prometheus, Dashboards)

**Objetivo:** entender como o projeto coleta e exibe observabilidade em três camadas: tracing (Langfuse), métricas (Prometheus), dashboards SQL (Grafana), com fallback gracefully quando infra ausente.

---

## Contexto

O projeto adota três pilares de observabilidade (todos **opt-in**, controlados por variáveis de ambiente):

| Camada | Propósito | Variável de configuração |
|--------|-----------|--------------------------|
| Langfuse | Tracing de execução (spans), proveniência (`prompt_digest`), score `diagnosis_correct` | `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` |
| Prometheus | Métricas customizadas por audiência (COI/IOC, SOC, iPaaS) | `PROMETHEUS_ENABLED=true` |
| Dashboards SQL | Visualização agregada de `incidents`, `llm_usage`, `system_contracts` | `DATABASE_URL` (via Grafana) |

**Pattern principal:** **fallback gracefully** — o diagnóstico continua funcionando mesmo sem infra de observabilidade (apenas logs + métricas zeradas).

---

## Langfuse — Tracing e Proveniência

### Arquivo principal: `app/agent/graph.py` (linhas 42–50)

```python
if settings.langfuse_configured:
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
    os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)
    os.environ.setdefault("LANGFUSE_BASE_URL", settings.langfuse_host)
else:
    os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
```

#### Fluxo de tracing (DA-53)

1. **Supervisor node** — span com `agent_domain` decidido deterministicamente (`"sap"`/`"saas"`/`"generic"`).
2. **Connector node** — span com `connector_type`, `is_mock`, `is_fallback` (DA-15/16).
3. **Retrieve node** — span com `hit["collection"]`, `hit["rerank_score_calibrated"]` (DA-25/42).
4. **Diagnose node** — span com chamada LLM e resposta estruturada (`DiagnosisModel`).
5. **Report node** — span com `evidence_strength`, `is_grounded`, `matched_source`, `abstained`.

#### Proveniência e score (DA-53)

A classe `DiagnosisModel` (app/models.py:136–207) inclui:

```python
prompt_digest: str | None = None  # Digest SHA-256 do prompt usado (DA-53)
prompt_version: str | None = None  # Versão do prompt (ex: "v2.1")
```

**Por que NULL quando rule engine vence?** — um diagnóstico sem LLM não foi produzido por prompt nenhum. Fallback `"desconhecido"` fabricaria proveniência falsa.

**Escrita do score `diagnosis_correct`:** via callback do Langfuse após humano verificar (`POST /incidents/{id}/verify`, DA-50):

```python
# app/services/incident_recorder.py::record_verification
if langfuse_client := get_client():
    langfuse_client.trace(update=TraceUpdate(body={
        "name": "diagnosis-verification",
        "output": {"diagnosis_correct": verified, ...}
    }))
```

#### Exercício prático

```bash
# AtivarLangfuse (via .env):
export LANGFUSE_PUBLIC_KEY=...
export LANGFUSE_SECRET_KEY=...
export LANGFUSE_HOST=https://cloud.langfuse.com

# Executar diagnóstico e verificar spans no dashboard:
uv run python -m app.cli.diagnose "IDoc 51 no SAP"

# Verificar proveniência no campo `prompt_digest` da resposta:
{
  "prompt_digest": "sha256:abc123...",
  "prompt_version": "v2.1",
  "evidence_strength": 1.0,
  ...
}
```

---

## Prometheus — Métricas Customizadas

### Arquivo principal: `app/metrics.py`

#### Métricas por audiência (DA-46/50/52)

```python
# COI/IOC — volume e qualidade de diagnósticos
DIAGNOSIS_TOTAL = Counter(
    "iic_diagnosis_total",
    "Total de diagnósticos processados",
    labelnames=["agent_domain", "llm_provider", "evidence_strength"],
)

DIAGNOSIS_LATENCY = Histogram(
    "iic_diagnosis_latency_seconds",
    "Latência do pipeline (segundos)",
    labelnames=["agent_domain", "llm_provider"],
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, float("inf")),
)

# SOC — segurança e privacidade
PII_DETECTED_TOTAL = Counter(
    "iic_pii_detected_total",
    "Total de incidentes com PII detectado",
    labelnames=["sensitivity_level"],
)

# iPaaS — saúde de conectores e circuit breaker
CONNECTOR_REQUEST_TOTAL = Counter(
    "iic_connector_request_total",
    "Total de chamadas a conectores",
    labelnames=["connector", "status"],  # success | error | mock
)

CIRCUIT_BREAKER_OPEN_TOTAL = Counter(
    "iic_circuit_breaker_open_total",
    "Número de vezes que o circuit breaker abriu",
    labelnames=["target"],
)
```

#### Setup no lifespan (app/main.py)

```python
# app/main.py (setup_metrics chamado durante lifespan)
def setup_metrics(app: FastAPI) -> None:
    from app.config import settings

    if not getattr(settings, "prometheus_enabled", False):
        logger.debug("Prometheus desabilitado.")
        return

    Instrumentator(
        excluded_handlers=["/health", "/ready", "/metrics"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
```

**Fallback:** quando `PROMETHEUS_ENABLED=false` (default), `metrics.py` define **stubs nulas** para todas as métricas (linhas 124–148), garantindo zero impacto no runtime.

#### Exercício prático

```bash
# Habilitar Prometheus:
export PROMETHEUS_ENABLED=true
export PROMETHEUS_PORT=9090  # ou outra porta livre

# Iniciar servidor:
uv run uvicorn app.main:app --reload

# Consultar métricas:
curl http://localhost:8000/metrics | grep "iic_diagnosis"

# Resultado esperado (exemplo):
# iic_diagnosis_total{agent_domain="sap",llm_provider="ollama",evidence_strength="high"} 1
# iic_diagnosis_latency_seconds_bucket{agent_domain="sap",llm_provider="ollama",le=5.0} 2
# iic_diagnosis_latency_seconds_sum{agent_domain="sap",llm_provider="ollama"} 2.3
```

---

## Dashboards SQL — Grafana

### Arquivo principal: `app/evaluation/coverage.py`, `scripts/generate_reports.py`

#### Modelo `incidents` (DA-50)

Tabela principal para dashboards:

```sql
-- app/models.py + alembic/002 (evidence_strength) + 005 (system_contracts) + 007 (web_users)
CREATE TABLE incidents (
    id UUID PRIMARY KEY,
    incident_id VARCHAR NOT NULL,
    connector_source_system VARCHAR,              -- foreign key-like (DA-50)
    agent_domain VARCHAR CHECK (agent_domain IN ('sap', 'saas', 'generic')),
    diagnosis_correct BOOLEAN,
    verified_at TIMESTAMPTZ,
    evidence_strength FLOAT,
    is_grounded BOOLEAN,
    prompt_digest VARCHAR,                        -- DA-53
    prompt_version VARCHAR,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### Exemplo de query (DA-50/52/58)

```sql
-- Taxa de acerto por conector, segmentada por `agent_domain`
SELECT
    i.connector_source_system,
    i.agent_domain,
    COUNT(*) FILTER (WHERE i.diagnosis_correct = true) * 100.0 / COUNT(*) AS accuracy_pct
FROM incidents i
WHERE i.verified_at IS NOT NULL
GROUP BY i.connector_source_system, i.agent_domain
ORDER BY i.connector_source_system, i.agent_domain;
```

#### scripts/generate_reports.py (DA-25/29/51)

Gera CSV com métricas agregadas para importar no Excel/Grafana:

```python
# Aplicaqueries SQL contra DATABASE_URL e escreve CSV
def generate_reports() -> None:
    session = get_sync_session_factory()()
    query = """
        SELECT
            date_trunc('day', i.created_at) AS day,
            COUNT(*),
            AVG(EXTRACT(EPOCH FROM (i.verified_at - i.created_at))) AS avg_verification_seconds
        FROM incidents i
        WHERE i.verified_at IS NOT NULL
        GROUP BY day
        ORDER BY day
    """
    results = session.execute(text(query)).fetchall()
    # Export to CSV...
```

#### Exercício prático

```bash
# Gerar relatório CSV de métricas:
uv run python -m scripts.generate_reports

# Importar CSV no Excel/Grafana:
# - Coluna 1: `day` (data)
# - Coluna 2: `count` (diagnósticos por dia)
# - Coluna 3: `avg_verification_seconds` (tempo médio de verificação)

# Query alternativa (DA-58: cobertura por conector):
SELECT
    i.connector_source_system,
    c.connector_type,
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE i.diagnosis_correct = true) AS correct,
    COUNT(*) FILTER (WHERE i.diagnosis_correct IS NULL) AS unverified
FROM incidents i
LEFT JOIN integration_systems c ON i.connector_source_system = c.system_key
GROUP BY i.connector_source_system, c.connector_type
ORDER BY total DESC;
```

---

## Circuit Breaker e Gateway (integração com métricas)

### Pontos de integração

| Módulo | Papel | Metrica associada |
|--------|-------|-------------------|
| `app/llm/gateway.py` | Policy + budget + circuit breaker (DA-26/41) | `CIRCUIT_BREAKER_OPEN_TOTAL`, `LLM_FALLBACK_TOTAL` |
| `app/agent/escalation.py` | Sinal determinístico de escalonamento (DA-44) | `DIAGNOSIS_LATENCY` (tier 3 vs. local) |
| `app/contracts/observe.py` | Detector de drift (DA-52) | `SENSITIVE_INCIDENT_TOTAL` (breaking → incidente) |

#### Fluxo de escala (DA-44)

1. **Rule engine vence** → `evidence_strength=1.0`, sinal `grounded`, `escalate=False`.
2. **RAG + LLM, evidência forte** (`rerank_score_calibrated >= 0.45`) → escala `False`.
3. **RAG fraca ou abstenção** (`rerank_score_calibrated < threshold` ou `abstained=True`) → sinal `curated_tier_weak`, `escalate=True`.

**Invariante:** o sinal de escala **nunca** usa autoavaliação do LLM (apenas fatos observáveis: `is_mock`, `collection`, `rerank_score_calibrated`, `abstained`, DA-33/44).

---

## Fallback Graceful — O que acontece sem infra?

| Infra ausente | Comportamento |
|---------------|---------------|
| `LANGFUSE_SECRET_KEY` não configurado | Tracing desligado (`LANGFUSE_TRACING_ENABLED=false`), logs normais |
| `PROMETHEUS_ENABLED=false` (default) | Métricas stubs nulas (nenhum overhead) |
| `DATABASE_URL` não configurado | `/metrics` não expõe `/precomputed-metrics`, `incidents` gravado em memória (perdido no restart) |

**Impacto no diagnóstico:** zero. Observabilidade é **aditiva**, não dependência crítica.

---

## Checklist de prática

1. **Configurar Langfuse** → `export LANGFUSE_*`, `uv run python -m app.cli.diagnose`, verificar spans no dashboard.
2. **Habilitar Prometheus** → `export PROMETHEUS_ENABLED=true`, `curl localhost:8000/metrics`.
3. **Query SQL em `incidents`** → `psql $DATABASE_URL -c "SELECT..."`, verificar métricas agregadas por `agent_domain`/`connector_source_system`.
4. **Verificar fallback** → executar diagnóstico sem infra, garantir que continua funcionando e apenas logs simples (sem warnings de infra).

---

## Referências rápidas às DAs

- **DA-15/16/25:** Evidence/Trust Layer (`evidence_strength`, `is_grounded`, `rerank_score_calibrated`).
- **DA-26:** AI Gateway (policy, circuit breaker, budget).
- **DA-41:** Circuit breaker (Redis compartilhado ou fallback memory).
- **DA-44:** Escalonamento determinístico (3 tiers, sinal aditivo).
- **DA-50:** Correlação incidente ↔ sistema (`connector_source_system`/`system_key`).
- **DA-51:** Quality gates (dataset, corpus, invariante do reranker, **documentação**).
- **DA-52/53:** Proveniência (`prompt_digest`), drift detector (`system_contracts`).
- **DA-58:** Mapa de cobertura produto × mecanismo (calculado, não mantido à mão).
- **DA-59:** Multi-vendor connectors (padrão único `fetch → result`).

---

## Próximos passos

- **Aula 7:** RAG com Qdrant (ingestão, busca híbrida, reranker, evidence admission).
- **Aula 8:** LLMs, gateway e governança (factory, prompts, policies).
- **Aula 9:** LangGraph e estado (state, graph, roteamento determinístico).
- **Aula 10:** Nós, Rule Engine e supervisão (nodes, rules, escalation).
