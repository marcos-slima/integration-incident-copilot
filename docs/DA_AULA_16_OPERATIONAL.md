# DA_AULA_16_OPERATIONAL.md — Observability & Operations (Módulo 16)

## Objetivo

Documentar os mecanismos de observabilidade e operações do Integration Incident Copilot, incluindo:
- Métricas Prometheus com segmentação COI/IOC, SOC, iPaaS
- Monitoramento de tokens e uso de LLM (DA-48)
- Geração de relatórios agendados (diários/semanais) em Excel/MD
- Dashboards Grafana segmentados por público-alvo
- Troubleshooting guide (TROUBLESHOOTING.md)

---

## Arquitetura

### Métricas Prometheus (DA-47)

O módulo `app/metrics.py` expõe métricas Prometheus via `prometheus-fastapi-instrumentator` com os seguintes segmentos:

| Métrica | Labels | Finalidade |
|---|---|---|
| `iic_diagnosis_duration_seconds` | `iic_diagnosis_status`, `iicpii_flag`, `iic_connector`, `iic_circuit_state` | Tempo de diagnóstico por status (success/failure/rule_engine), flag PII, conector e estado do circuit breaker |
| `iic_llm_calls_total` | `iic_provider`, `iic_model`, `iic_success` | Chamadas ao LLM por provedor (Ollama/OpenAI/Azure), modelo e sucesso/falha |
| `iic_circuit_breaker_events_total` | `iic_circuit_state`, `iic_origin` | Eventos do circuit breaker por estado (closed/open/half_open) e origem (DA-41) |
| `iic_token_usage_total` | `iic_provider`, `iic_model` | Uso de tokens por provedor e modelo (DA-48) |

**Observação:** Labels permitem agregação por público:
- **COI/IOC**: filtra por `iic_connector` (SAP OData/RFC/etc.)
- **SOC**: filtra por `iicpii_flag` (verdadeiro = caso de segurança)
- **iPaaS**: filtra por `iic_diagnosis_status` (ex: workflow/hybrid)

### Monitoramento de Tokens (DA-48)

`app/admin/metering.py` fornece o callback `LLMMeteringCallbackHandler` que captura:
- `generation_info["token_usage"]` (OpenAI, Ollama e outros que expõem o campo)
- Persistência via `record_usage(prompt_tokens, completion_tokens, total_tokens, provider, model)`
- **Best-effort**: não há heurísticas de fallback quando o campo não está presente

**Exemplo de uso:**

```python
# app/llm/factory.py -> invoke_with_hybrid_fallback()
callbacks = [
    LLMMeteringCallbackHandler(
        record_usage=partial(record_usage, origin=origin, model=model, provider=provider)
    )
]
```

### Relatórios Agendados

`scripts/generate_reports.py` gera(relatórios diários e semanais):

| Formato | Padrão | Público |
|---|---|---|
| Excel (.xlsx) | COI_SAP_iPaaS_report.xlsx | COI (comparativo mensal, métricas de drift) |
| Markdown (.md) | SOC_report.md | SOC (incidentes, SLA, raízes de diagnóstico) |

**Queries agrupadas por público:**
- **COI/IOC**: volume de incidentes, SLA, acuracidade, raízes de diagnóstico, uso de tokens
- **SOC**: incidentes de segurança, PII redaction, compliance (GDPR/HIPAA)
- **iPaaS**: integrações SAP, conectores, endpoints, drift de contrato

**Usage:**

```bash
uv run python scripts/generate_reports.py --period daily --output-dir ./reports
uv run python scripts/generate_reports.py --period weekly --output-dir ./reports
```

### Dashboards Grafana (DA-47)

Quatro dashboards segmentados por público (JSON em `deploy/grafana/dashboards/`):

| Dashboard | UID | Métricas-chave |
|---|---|---|
| COI/IOC | `dashboard_coi.json` | Incidentes hoje/semana, taxa de verificação, acuracidade,.tokens.usados |
| SOC | `dashboard_soc.json` | PII redaction, SLA, acuracidade por conector, erros criticos |
| iPaaS | `dashboard_ipaas.json` | Conectores ativos, endpoints, drifts, tempo médio de diagnóstico |
| Systems | `dashboard_systems.json` | Sistema por conector, uso por origin, circuit breaker state |

**Dados:** todos consultam Postgres via datasource `postgres_iic`.

---

## Fixtures

Nenhum fixture específico deste módulo (dados usados são os do sistema de incidentes).

---

## Padrões

### Métricas

- Segmentação obrigatória por `iic_connector`, `iicpii_flag`, `iic_diagnosis_status`
- Circuit breaker events devem incluir `iic_origin` (DA-41)
- Token usage deve incluir `iic_provider` e `iic_model` (DA-48)

### Dashboards

- Thresholds configurados por público (ex: COI tolera até 20% drift; SOC exige 100% sem PII)
- Visualizações: stat (kpi), timeseries (volume), heatmap (raízes de diagnóstico), gauge (SLA)

### Relatórios

- Excel: planilhas separadas por público (COI, SOC, iPaaS)
- Markdown: template Jinja2 com seções pré-definidas (executivo, técnico, appendices)

---

## Gates

Nenhum gate específico deste módulo (os gates de qualidade estão em `app/evaluation/gates.py` e verificam integridade da documentação, cobertura de conectores, etc.).

---

## Exercícios

1. **Startar infra de observabilidade:**

```bash
docker compose --profile observability up -d postgres grafana
# Verify: curl -sf http://localhost:3001/api/health (Grafana)
```

2. **Gerar relatório diário:**

```bash
DATABASE_URL=postgresql://iic:REQUIRED_SET_IN_ENV@127.0.0.1:5432/iic \
uv run python scripts/generate_reports.py --period daily --output-dir ./reports
```

3. **Verificar métricas Prometheus:**

```bash
curl -sf http://localhost:8000/metrics | grep iic_
```

4. **Exercitar dashboards:**
   - Selecionar COI dashboard → filtrar por conector OData → visualizar SLA
   - Selecionar SOC dashboard → filtrar por `pii_flag=true` → verificar PII redaction
   - Selecionar Systems dashboard → ver uso por origin → comparar tokens porprovider

---

## Invariantes

1. **Tokens são contados via `generation_info`** — não há heurística de estimativa (DA-48)
2. **Métricas segmentadas por públicos** — COI/IOC, SOC, iPaaS devem ser distinguishable via labels
3. **Dashboards consultam Postgres diretamente** — não há aggregation layer intermediária
4. **Relatórios agendados não interferem no runtime** — cron job contêiner separado (`reporter` service)

---

## Limitações

1. **Token metering best-effort** — provedores que não expõem `generation_info["token_usage"]` não são contabilizados
2. **Relatórios não em tempo real** — processamento agendado (diário/semanal), não streaming
3. **Dashboards sem alertas** — visualização, não notificação automática
4. **Grafana não provisionado via TDD** — provisioning via files, não API

---

## Referências

- DA-26: AI Gateway (policy + circuit breaker + budget)
- DA-41: Circuit breaker com backend Redis
- DA-47: Registro gerenciado de models/credenciais por ORIGEM (LLM_REGISTRY_DB)
- DA-48: Metering de tokens REAIS (real, não estimativa)
- TROUBLESHOOTING.md: 9 tópicos (startup, Qdrant, Ollama, DB, LLM, auth, CORS, timeout, CI)
- DEPLOYMENT.md: deployment no Kyma (DA-24)
- DEPLOY.md: guia de deploy (DA-24)
- `app/metrics.py`: métricas Prometheus
- `app/admin/metering.py`: callback para token metering
- `scripts/generate_reports.py`: geração de relatórios
- `deploy/grafana/dashboards/*.json`: dashboards segmentados
- `docker-compose.yml`: `grafana`service + profile `observability`

---

## DAs relevantes

| DA | O que é | Onde |
|---|---|---|
| DA-26 | AI Gateway (policy + circuit breaker + budget) | `app/llm/gateway.py` |
| DA-41 | Circuit breaker com backend Redis | `app/circuit_breaker.py`, `app/a2a/task_store.py` |
| DA-47 | Registro gerenciado de models/credenciais por ORIGEM | `app/admin/` |
| DA-48 | Metering de tokens REAIS (real, não estimativa) | `app/admin/metering.py` |
| DA-50 | Correlação `incidents` ↔ `integration_systems` por `system_key` | `app/admin/correlation.py` |
