# DA_AULA_20_MONITORING.md — Production Monitoring & Alerting (Módulo 20)

## Objetivo

Documentar mecanismos de produção para monitoramento e alerting do Integration Incident Copilot:
- Alertas baseados em thresholds de SLA/incidentes críticos
- Thresholds configuráveis por público (COI/SOC/iPaaS)
- Integração com ferramentas de notificação (webhook, email, Slack)
- Custom dashboards para alertas (ex: "incidentes com SLA violado")
- Alarme em Grafana via alerting rules (DA-47)

---

## Arquitetura

### Alert Thresholds por Público

A tabela `incidents` tem colunas que permitem calcular SLA e acuracidade em tempo real. thresholds são configuráveis por público:

| Público | KPI | Threshold warning | Threshold critical | Query base |
|---|---|---|---|---|
| **COI** | SLA diagnostics (p95) | > 30s | > 60s | `PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)` |
| **COI** | Acuracidade diagnóstico | < 85% | < 70% | `COUNT(diagnosis_correct=true) / COUNT(diagnosis_correct IS NOT NULL)` |
| **SOC** | PII não redigida | > 0 | > 0 | `COUNT(pii_detected=true AND redaction_applied=false)` |
| **SOC** | Incidentes CRITICAL | > 5/dia | > 10/dia | `COUNT(sensitivity_level='critical')` |
| **iPaaS** | Drifts de contrato (breaking) | > 2/semana | > 5/semana | `COUNT(drift_severity='breaking')` |

**Configuração:** variáveis de ambiente ou via UI admin (DA-47):
```env
SLA_P95_WARNING=30000
SLA_P95_CRITICAL=60000
ACCURACY_WARNING=85
ACCURACY_CRITICAL=70
PII_UNREDACTED_WARNING=0
PII_UNREDACTED_CRITICAL=0
CRITICAL_INCIDENTS_DAILY_WARNING=5
CRITICAL_INCIDENTS_DAILY_CRITICAL=10
```

### Queries por Público (Grafana)

Dashboards já existentes usam queries SQL que podem ser convertidas em alertas:

**COI dashboard:**
```sql
-- SLA p95
SELECT
  ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)) AS p95_ms
FROM incidents WHERE $__timeFilter(created_at)

-- Acuracidade
SELECT
  ROUND(100.0 * COUNT(diagnosis_correct) FILTER (WHERE diagnosis_correct) /
        NULLIF(COUNT(diagnosis_correct), 0), 1) AS accuracy_pct
FROM incidents WHERE $__timeFilter(created_at)
```

**SOC dashboard:**
```sql
-- PII não redigida
SELECT COUNT(*) AS unredacted
FROM incidents
WHERE pii_detected AND NOT redaction_applied
  AND $__timeFilter(created_at)

-- Incidentes CRITICAL
SELECT COUNT(*) AS critical
FROM incidents
WHERE sensitivity_level = 'critical'
  AND $__timeFilter(created_at)
```

**iPaaS dashboard:**
```sql
-- Drifts breaking
SELECT
  COUNT(*) FILTER (WHERE drift_severity = 'breaking') AS breaking_drifts
FROM system_contracts
WHERE created_at >= NOW() - INTERVAL '7 days'
```

### Notificação (Webhook + Email + Slack)

**Webhook (DA-23):**
```bash
# Teste: curl -X POST http://localhost:8000/webhooks/alert \
#   -H "Content-Type: application/json" \
#   -d '{"alert_type": "sla_breach", "severity": "critical", "value": 75000, "threshold": 60000}'
```

**Email (SMTP, não implementado no escopo atual):**
```python
# app/services/alert_email.py (futuro)
def send_sla_breach_alert(p95_ms: int, threshold_ms: int, region: str) -> None:
    ...
```

**Slack (via webhook):**
```python
# app/services/alert_slack.py (futuro)
def notify_slack(message: str, channel: str = "#ops-alerts") -> None:
    ...
```

### Rules Engine para Alertas (DA-33, futura expansão)

Current rule engine (DA-33) detecta raízes de diagnóstico. Pode ser expandido para incluir:
- `rule_sla_p95_exceeded` → chama `alert_sla_breach()`
- `rule_sensitivity_critical` → chama `alert_critical_incident()`
- `rule_drift_breaking` → chama `alert_drift_breaking()`

---

## Fixtures

Nenhum fixture específico para este módulo (dados usados são os do sistema de incidentes).

---

## Padrões

### Alerting

- **Thresholds separados por público** — COI tolera até 60s SLA; SOC exige 0% PII não redigida
- **Severity level** — warning vs critical (dois níveis, não três — evitar alarme falso)
- **Cooldown** — evitar spam: 5 minutos entre alerts do mesmo tipo para o mesmo incidente
- **acknowledgement** — alerta pode ser acknowledged via `/admin/alerts/{id}/ack`

### Dashboards

- Panel com **thresholds visíveis** (ex: linha vermelha no gráfico) para fácil verificação
- Status global: "Green/.Yellow/Red" por público, baseado no worst alert
- History: últimos 24h de alerts (timeline vertical)

### Notification

- Webhook é o único método **implemented** (DA-23)
- Email/Slack são planejados (futuro)
- Mensagem webhook inclui: alert_type, severity, value, threshold, timestamp, region, incident_id

---

## Gates

| Gate | Description | Source |
|---|---|---|
| `sla_threshold_configured` | Verifica variáveis `SLA_P95_WARNING` e `SLA_P95_CRITICAL` (ambas, não só uma) | `scripts/quality_gate.py::check_sla_thresholds()` |
| `pii_threshold_configured` | Verifica `PII_UNREDACTED_WARNING` e `PII_UNREDACTED_CRITICAL` | `scripts/quality_gate.py::check_pii_thresholds()` |
| `critical_threshold_configured` | Verifica `CRITICAL_INCIDENTS_DAILY_WARNING` e `CRITICAL_INCIDENTS_DAILY_CRITICAL` | `scripts/quality_gate.py::check_critical_thresholds()` |
| `dashboard_alerting_queries` | Valida que todos os painéis têm queries que podem virar alertas (ex: SQL válido, agregação) | `scripts/validate_dashboards.py` |

**Observação:** Gates de configuração de thresholds são **warnings** (não erros) — o sistema funciona sem alertas, só não gera alarme.

---

## Exercícios

1. **Verificar thresholds configurados:**
   ```bash
   # Startar infra
   docker compose --profile observability up -d postgres grafana

   # Verificar variáveis no .env
   grep SLA_P95 .env
   grep PII_UNREDACTED .env

   # Verificar dashboards com threshold visuals
   curl -sf http://localhost:3001/api/dashboards/uid/coi | jq '.dashboard.panels[] | select(.title | contains("SLA"))'
   ```

2. **Testar query de SLA p95 contra Postgres:**
   ```bash
   DATABASE_URL=postgresql://iic:REQUIRED_SET_IN_ENV@127.0.0.1:5432/iic \
   uv run python -c "
   import psycopg2
   conn = psycopg2.connect('postgresql://iic:REQUIRED_SET_IN_ENV@127.0.0.1:5432/iic')
   cur = conn.cursor()
   cur.execute('SELECT ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)) FROM incidents WHERE created_at >= NOW() - INTERVAL \\'7 days\\'')
   print('P95 latency (ms):', cur.fetchone()[0])
   "

   # Expected: > 0 se houver dados, ou 0 se tabela vazia
   ```

3. **Validar dashboard queries (DA-50):**
   ```bash
   uv run python scripts/validate_dashboards.py
   # Expected: SUCCESS: all 45 queries validated for 4 dashboards
   ```

4. **Simular alerta de SLA (via webhook mock):**
   ```bash
   # Startar servidor webhook fake (ex: https://webhook.site)
   # Verificar logs do FastAPI ao disparar um incidente com SLA alta
   uv run uvicorn app.main:app --reload &
   # Trigger diagnose com description longa (simular slow connector)
   curl -sf -X POST http://localhost:8000/diagnose \
     -H "X-API-Key: ${API_KEY}" \
     -H "Content-Type: application/json" \
     -d '{
       "interface_type": "odata",
       "connector_source_system": "SAP S/4HANA",
       "description": "OData endpoint lento: timeout em 60s para consulta de pedidos"
     }'
   # Check logs para verifiqueção de threshold check
   ```

5. **Custom dashboard para "SLA Violated" (opcional):**
   ```json
   // deploy/grafana/dashboards/dashboard_sla_violated.json
   {
     "panels": [
       {
         "title": "Incidentes com SLA > 60s (critical)",
         "targets": [{
           "rawSql": "SELECT COUNT(*) FROM incidents WHERE latency_ms > 60000 AND $__timeFilter(created_at)"
         }]
       }
     ]
   }
   ```

---

## Invariantes

1. **Thresholds separados por público** — Não há threshold único global (COI/SOC/iPaaS têm limits diferentes)
2. **Alerts não quebram o diagnóstico** — Falha de notificação logada, não interrompe o fluxo (best-effort)
3. **Threshold warning < critical** — NUNCA threshold warning > critical
4. **Cooldown obrigatório** — Mesmo alert type não dispara mais de uma vez a cada 5 minutos
5. **Thresholds são variáveis de ambiente, não hardcoded** — Configuração via `.env` ou UI admin (DA-47)

---

## Limitações

1. **Notificação apenas webhook implemented** — Email/Slack são planejados (futuro)
2. **Sem acknoledgement UI** — `/admin/alerts/{id}/ack` planejado, não implementado
3. **Sem histórico de alertas** — Tabela `alert_history` planejada (futuro)
4. **Sem predição de SLA** — Monitoramento reativo, não proativo (machine learning)
5. **Sem auto-remediation** — Alerta, não corrige (ex: escalonar, trigger fallback)
6. **Sem canais múltiplos por alert type** — Apenas um webhook por tipo de alerta

---

## Referências

- DA-23: Event Mesh via webhook CloudEvents → `run_diagnosis()`
- DA-33: Rule Engine determinístico (pré-filtro LLM, 21 regras SAP) — expansão futura
- DA-47: Registro gerenciado de models/credenciais por ORIGEM (LLM_REGISTRY_DB)
- DA-50: Correlação `incidents` ↔ `integration_systems` por `system_key`
- TROUBLESHOOTING.md: 9 tópicos (startup, Qdrant, Ollama, DB, LLM, auth, CORS, timeout, CI)
- DEPLOYMENT.md: deployment no Kyma (DA-24)
- DEPLOY.md: guia de deploy (DA-24)
- `app/metrics.py`: métricas Prometheus (DA-47)
- `app/admin/metering.py`: callback para token metering (DA-48)
- `scripts/generate_reports.py`: geração de relatórios (DA-47)
- `scripts/validate_dashboards.py`: validação de queries SQL em dashboards (DA-50)
- `app/redaction.py`: redaction de PII (DA-30)
- `app/config.py`: variáveis de ambiente `SLA_P95_*`, `PII_UNREDACTED_*`, `CRITICAL_*`
- `deploy/grafana/dashboards/*.json`: dashboards segmentados (COI/SOC/iPaaS/Systems)

---

## DAs relevantes

| DA | O que é | Onde |
|---|---|---|
| DA-23 | Event Mesh via webhook CloudEvents | `app/events/consumer.py` |
| DA-30 | PII redaction ampliado | `app/redaction.py` |
| DA-33 | Rule Engine determinístico (21 regras SAP) | `app/agent/rules.py` |
| DA-47 | Registro gerenciado de models/credenciais por ORIGEM | `app/admin/` |
| DA-48 | Metering de tokens REAIS (real, não estimativa) | `app/admin/metering.py` |
| DA-50 | Correlação `incidents` ↔ integration_systems por `system_key` | `app/admin/correlation.py` |
