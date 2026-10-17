# DA-70: Métricas FinOps e dashboards

## Status: Concluído (implementação básica)

## Objetivo
Adicionar métricas FinOps para monitorar custos e otimizar uso do Copilot.

## Métricas necessárias (DA-70)

### Métricas FinOps
- `llm_requests_total`: contagem de chamadas LLM por provider
- `llm_tokens_total`: tokens (in/out) por provider
- `llm_cost_usd_total`: custo em USD por provider
- `llm_budget_deviation_ratio`: desvio estimado/real (JÁ EXISTE)
- `diagnosis_duration_seconds`: duração por domínio (JÁ EXISTE como `iic_diagnosis_latency_seconds`)

### Métricas existentes (app/metrics.py)
- `iic_diagnosis_total`: Total de diagnósticos processados
- `iic_diagnosis_latency_seconds`: Latência do pipeline de diagnóstico
- `iic_diagnosis_verified_total`: Total de diagnósticos verificados
- `iic_pii_detected_total`: Total de incidentes onde PII foi detectado
- `iic_redaction_applied_total`: Total de incidentes onde redação de PII foi aplicada
- `iic_sensitive_incident_total`: Total de incidentes classificados como confidential/secret
- `iic_connector_request_total`: Total de chamadas a conectores externos
- `iic_circuit_breaker_open_total`: Número de vezes que o circuit breaker abriu
- `iic_circuit_breaker_state`: Estado atual do circuit breaker
- `iic_llm_fallback_total`: Total de ativações do fallback híbrido
- `iic_rule_engine_hit_total`: Total de diagnósticos resolvidos pelo Rule Engine
- `iic_llm_budget_deviation_ratio`: Desvio entre custo estimado e real (JÁ EXISTE)
- `iic_llm_budget_exceeded_total`: Total de chamadas LLM que excederam o orçamento

## Métricas implementadas (DA-70)

### 1. llm_requests_total ✅
- **Descrição**: Contagem de chamadas LLM por provider
- **Labels**: `provider`, `model`
- **Fonte**: `app/llm/gateway.py::invoke_via_gateway()`
- **Status**: Implementado

### 2. llm_tokens_total ✅
- **Descrição**: Tokens (in/out) por provider
- **Labels**: `provider`, `model`, `token_type` (input/output)
- **Fonte**: `app/llm/gateway.py::invoke_via_gateway()` + callback de usage
- **Status**: Implementado

### 3. llm_cost_usd_total ✅
- **Descrição**: Custo em USD por provider
- **Labels**: `provider`, `model`
- **Fonte**: `app/llm/gateway.py::invoke_via_gateway()` + metering (DA-48)
- **Status**: Implementado

## Implementação concluída

### 1. Atualizar app/metrics.py ✅
- Adicionar métricas `LLM_REQUESTS_TOTAL`, `LLM_TOKENS_TOTAL`, `LLM_COST_USD_TOTAL`
- Criar funções `observe_llm_request()`, `observe_llm_tokens()`, `observe_llm_cost()`
- Garantir compatibilidade com DA-48 (metering)

### 2. Atualizar app/llm/gateway.py ✅
- Incrementar `llm_requests_total` em `invoke_via_gateway()`
- Incrementar `llm_tokens_total` com base no usage_metadata
- Incrementar `llm_cost_usd_total` com base no custo calculado

### 3. Atualizar dashboards
- Estender `dashboard_coi.json` com métricas FinOps (PENDENTE)
- Validar com `scripts/validate_dashboards.py` (PENDENTE)

### 4. Documentar limites
- Declarar o que não é mensurável (ex: custo de retrieval, custo de GraphRAG)

## Arquivos modificados
- `app/metrics.py`: adicionar métricas FinOps
- `app/llm/gateway.py`: incrementar métricas em `invoke_via_gateway()`

## Notas
- Usar o mesmo padrão das métricas existentes
- Labels devem ter cardinalidade baixa (<10 valores por label)
- Métricas nunca devem levantar exceções (observação, não lógica de negócio)
- Integrar com DA-48 (metering) para obter custos reais

## Próximos passos
- Estender dashboards existentes
- Validar com `scripts/validate_dashboards.py`
- Declarar limites do que não é mensurável
- Criar testes para métricas FinOps

## Validação
- Quality gate: 21/21 checks ✅
- Testes: 1553 passando ✅
- Branch: docs/en-translation (commit 6a326c0) ✅
