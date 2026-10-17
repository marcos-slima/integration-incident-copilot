# Tarefa 0: Confirmação do Estado Atual ✅
- [x] Verificar todas as premissas no código
- [x] Confirmar tabela de estado atual
- [x] Identificar divergências (nenhuma encontrada)

# Tarefa 1: DA-66 — Routing Decision explícita e auditável ✅
- [x] Criar `app/llm/routing.py` com Policy Engine puro
- [x] Definir `RoutingRequest` e `RoutingDecision` (Pydantic models)
- [x] Implementar duas fases: elegibilidade rígida → scoring de preferências
- [x] Reutilizar funções existentes (`_select_allowed_providers`, `_provider_allows_sensitivity`, `capabilities_for_origin`)
- [x] Emitir decisão no audit log estruturado e como metadata do trace Langfuse
- [x] Expor em `GET /llm/policy` (já existe via `describe_effective_policy`)
- [x] Criar testes para cada motivo de rejeição
- [x] Garantir comportamento idêntico ao v1 com config default
- [x] Mensagem de erro inclui origem do provider e modo de soberania
- [x] `describe_effective_policy` expõe `routing.allowed_for_confidential/public`
- [x] Atualizar testes para verificar origem na mensagem de erro
- [x] Registrar DA-66 em CLAUDE.md, DECISOES_DE_ARQUITETURA.md, ARCHITECTURE.md
- [x] Quality gate aprovado (21/21 checks)
- [x] Todos os 1525 testes unitários passando

# Tarefa 2: DA-67 — Token budgeting e orçamento por tarefa
- [ ] Substituir `len//4` por contagem por família quando disponível
- [ ] Preço: preferir `price_{in,out}_per_1m` do registry sobre heurística
- [ ] Budget ledger por diagnóstico (`run_diagnosis`)
- [ ] Registrar estimado × real (metering)
- [ ] Criar testes de esgotamento de orçamento
- [ ] Expor desvio estimado/real como métrica Prometheus

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
