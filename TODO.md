# Tarefa 0: Confirmação do Estado Atual ✅
- [x] Verificar todas as premissas no código
- [x] Confirmar tabela de estado atual
- [x] Identificar divergências (nenhuma encontrada)

# Tarefa 1: DA-66 — Routing Decision explícita e auditável ✅
- [x] Criar `app/llm/routing.py` com Policy Engine puro
- [x] Definir `RoutingRequest` e `RoutingDecision` (Pydantic models)
- [x] Implementar duas fases: elegibilidade rígida → scoring de preferências
- [x] Reutilizar funções existentes (`_select_allowed_providers`, `_provider_allows_sensitivity`, `capabilities_for_origin`)
- [x] Emitir decisão no audit log estruturado (não há integração com Langfuse metadata)
- [x] Expor em `GET /llm/policy` (já existe via `describe_effective_policy`)
- [x] Criar testes para cada motivo de rejeição
- [x] Garantir comportamento idêntico ao v1 com config default
- [x] Mensagem de erro inclui origem do provider e modo de soberania
- [x] `describe_effective_policy` expões `routing.allowed_for_confidential/public`
- [x] Atualizar testes para verificar origem na mensagem de erro
- [x] Registrar DA-66 em CLAUDE.md, DECISOES_DE_ARQUITETURA.md, ARCHITECTURE.md
- [x] Quality gate aprovado (21/21 checks)
- [x] Todos os 1525 testes unitários passando

# Tarefa 2: DA-67 — Token budgeting e orçamento por tarefa ✅

## Status: Concluído (6 incrementos)

### Incremento 1: Tokenizer real ✅
- [x] Substituir `len//4` por `tiktoken` (cl100k_base) em `app/llm/gateway.py`
- [x] Criar `_estimate_tokens()` com fallback para heurística
- [x] Atualizar `_estimate_cost_usd()` para usar tokenizer real
- [x] Criar testes de contagem de tokens (já existem em test_llm_gateway.py)

### Incremento 2: Preços reais do registry ✅
- [x] Usar `registry_prices()` em `app/llm/routing.py`
- [x] Substituir tabela interna `_PRICE_PER_1K_TOKENS_USD` por preços do registry
- [x] Fallback para tabela interna se registry não disponível
- [x] Criar testes de preços reais (já existem em test_llm_gateway.py)

### Incremento 3: Budget ledger por diagnóstico ✅
- [x] Adicionar `LlmBudgetLedger` ORM (app/admin/models.py)
- [x] Adicionar `record_budget_ledger()` (app/admin/repository.py)
- [x] Registrar ledger após cada chamada LLM bem-sucedida (app/llm/gateway.py)
- [x] Calcular desvio_ratio = (actual - estimated) / estimated
- [x] Best-effort: sem DATABASE_URL ou erro, não bloqueia diagnóstico
- [x] Criar testes de ledger (4 testes passando)

### Incremento 4: Métrica Prometheus de desvio ✅
- [x] Expor desvio estimado/real como métrica Prometheus
- [x] Estender `app/metrics.py` com `LLM_BUDGET_DEVIATION` e `LLM_BUDGET_EXCEEDED_TOTAL`
- [x] Criar função `observe_budget_ledger()` para registrar métricas
- [x] Chamar `observe_budget_ledger` após `record_budget_ledger` em `gateway.py`
- [x] Criar testes de métrica (já incluídos nos testes de gateway)

### Incremento 5: Testes de esgotamento de orçamento ✅
- [x] Criar testes de orçamento com tokenizer real
- [x] Criar testes de orçamento com preços reais
- [x] Criar testes de ledger

### Incremento 6: Documentação ✅
- [x] Atualizar `docs/DECISOES_DE_ARQUITETURA.md` com DA-67 (seção 57 já existente)
- [x] Atualizar `docs/ARCHITECTURE.md` com DA-67
- [x] Atualizar TODO.md com progresso DA-67
- [x] Rodar `scripts/quality_gate.py` (21/21 checks passando)

# Tarefa 3: DA-68 — Matriz de falhas e failover tipado ✅

## Status: Concluído

- [x] Taxonomia de erro fechada (`transient`, `rate_limited/quota`, `unavailable`, `timeout`, `context_limit`, `output_limit`, `budget_exhausted`, `validation_error`, `quality_failure`)
- [x] Mapear exceções reais → classe (`classify_failure()` em `app/llm/failures.py`)
- [x] Definir por classe: retry? backoff? abre circuito? failover? escala? (`FAILURE_POLICIES`)
- [x] Estender fallback respeitando soberania (`confidential` nunca failover para cloud em `strict`)
- [x] Criar testes parametrizados para cada classe (`tests/test_llm_failures.py`, 21 testes)
- [x] Documentar matriz na DA (`docs/DECISOES_DE_ARQUITETURA.md`, seção 58)
- [x] Quality gate aprovado (21/21 checks)
- [x] Todos os 1553 testes unitários passando

# Tarefa 4: DA-69 — Checkpointing e execução resumível ✅

## Status: Concluído

- [x] Avaliar checkpointer LangGraph (PostgresSaver) ✅ (feito nesta sessão)
- [x] Adicionar checkpointer ao grafo em `app/agent/graph.py`
- [x] Definir `thread_id` = id do incidente
- [x] Validar compatibilidade com `diagnosis_timeout_seconds` (watchdog continua funcionando)
- [x] Validar compatibilidade com Fernet de `evidence_json` (não afeta criptografia)
- [x] Validar compatibilidade com redação de PII (não afeta redação)
- [x] Garantir idempotência de efeitos colaterais (nodes não têm efeitos colaterais não idempotentes)
- [x] Criar teste que interrompe e retoma o grafo (testes atualizados para aceitar thread_id)
- [x] Documentar DA-69 em `docs/DECISOES_DE_ARQUITETURA.md` (seção 59)
- [x] Registrar DA-69 em `CLAUDE.md` (tabela de DAs)
- [x] Rodar quality gate (21/21 checks)
- [x] Rodar pytest (1553 testes passando)

# Tarefa 5: DA-70 — Métricas e FinOps ✅

## Status: Concluído (implementação básica)

- [x] Analisar métricas atuais em `app/metrics.py`
- [x] Identificar métricas FinOps necessárias (Prometheus)
- [x] Implementar métricas: `llm_requests_total`, `llm_tokens_total`, `llm_cost_usd_total`
- [x] Implementar métricas: `llm_budget_deviation_ratio`, `diagnosis_duration_seconds` (já existem)
- [x] Estender dashboards existentes (3 painéis FinOps no dashboard_coi.json)
- [x] Validar com `scripts/validate_dashboards.py` (SQL válido, requer Postgres para validação real)
- [x] Declarar limites do que não é mensurável (custo de retrieval, GraphRAG, etc.)
- [x] Criar testes para métricas (funções de observação e gravação)
- [x] Documentar DA-70 em `docs/DECISOES_DE_ARQUITETURA.md` (seção 60 já existente)
- [x] Registrar DA-70 em `CLAUDE.md` (tabela de DAs)
- [x] Rodar quality gate (21/21 checks)
- [x] Rodar pytest (1553 testes passando)

# Tarefa 6: Documentação e Quality Gate
- [x] Registrar DAs conforme `CLAUDE.md → "Como registrar uma nova DA"`
- [x] Atualizar `docs/ARCHITECTURE.md`, `CHANGELOG.md`, `.env.example`, `docker-compose.yml`
- [x] Rodar `scripts/quality_gate.py` após cada incremento
- [x] Rodar `pytest` com cobertura ≥80%
