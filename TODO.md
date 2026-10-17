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

# Tarefa 2: DA-67 — Token budgeting e orçamento por tarefa

## Status: Em progresso (Incrementos 1 e 2 concluídos)

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
- [ ] Criar testes de ledger

### Incremento 4: Métrica Prometheus de desvio
- [ ] Expor desvio estimado/real como métrica Prometheus
- [ ] Estender `app/metrics.py`
- [ ] Criar testes de métrica

### Incremento 5: Testes de esgotamento de orçamento
- [ ] Criar testes de orçamento com tokenizer real
- [ ] Criar testes de orçamento com preços reais
- [ ] Criar testes de ledger

### Incremento 6: Documentação
- [ ] Atualizar `docs/DECISOES_DE_ARQUITETURA.md` com DA-67
- [ ] Atualizar `docs/ARCHITECTURE.md`
- [ ] Rodar `scripts/quality_gate.py`

# Tarefa 3: DA-68 — Matriz de falhas e failover tipado
- [ ] Taxonomia de erro fechada (`transient`, `rate_limited/quota`, `unavailable`, `timeout`, `context_limit`, `output_limit`, `budget_exhausted`, `validation_error`, `quality_failure`)
- [ ] Mapear exceções reais → classe
- [ ] Definir por classe: retry? backoff? abre circuito? failover? escala?
- [ ] Estender fallback respeitando soberania (`confidential` nunca failover para cloud em `strict`)
- [ ] Criar testes parametrizados para cada classe
- [ ] Documentar matriz na DA

# Tarefa 4: DA-69 — Checkpointing e execução resumível
- [ ] Avaliar checkpointer LangGraph (Postgres)
- [ ] Opt-in por flag, default OFF
- [ ] `thread_id` = id do incidente
- [ ] Retomada a partir do último nó concluído
- [ ] Validar compatibilidade com `diagnosis_timeout_seconds`, Fernet de `evidence_json`, redação de PII
- [ ] Idempotência de efeitos colaterais
- [ ] Criar teste que interrompe o grafo entre nós e retoma

# Tarefa 5: DA-70 — Métricas e FinOps
- [ ] Métricas da spec §6 viáveis com o que existe
- [ ] Dashboards: estender os existentes e passar em `scripts/validate_dashboards.py`
- [ ] Declarar limites do que não é mensurável

# Tarefa 6: Documentação e Quality Gate
- [x] Registrar DAs conforme `CLAUDE.md → "Como registrar uma nova DA"`
- [x] Atualizar `docs/ARCHITECTURE.md`, `CHANGELOG.md`, `.env.example`, `docker-compose.yml`
- [x] Rodar `scripts/quality_gate.py` após cada incremento
- [x] Rodar `pytest` com cobertura ≥80%
