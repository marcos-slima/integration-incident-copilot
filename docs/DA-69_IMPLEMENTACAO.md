# DA-69: Checkpointing e execução resumível

## Status: Em planejamento

## Objetivo
Adicionar checkpointing ao grafo LangGraph para permitir:
- Retomada de diagnósticos após interrupções (reinício de pod, crash, etc.)
- Human-in-the-loop workflows
- Time travel debugging
- Fault tolerance

## Implementação planejada

### 1. Adicionar checkpointer ao grafo
- Usar `PostgresSaver` (DA-19 já usa Postgres)
- `thread_id` = id do incidente (`incident_id` no state)
- Opt-in via flag `CHECKPOINTING_ENABLED=true` (default: OFF)

### 2. Integração com o grafo atual
- Modificar `build_graph()` para aceitar checkpointer opcional
- Modificar `run_diagnosis()` para passar `thread_id` no config
- Manter compatibilidade com execução atual (sem checkpointer)

### 3. Validar compatibilidade
- `diagnosis_timeout_seconds`: watchdog continua funcionando
- Fernet de `evidence_json`: checkpointing não afeta criptografia
- Redação de PII: checkpointing não afeta redação

### 4. Idempotência
- Verificar que nodes não têm efeitos colaterais não idempotentes
- Se necessário, adicionar idempotency keys ou upserts

### 5. Testes
- Criar teste que interrompe o grafo entre nós e retoma
- Validar que estado é restaurado corretamente

### 6. Documentação
- Atualizar `docs/DECISOES_DE_ARQUITETURA.md` com DA-69
- Atualizar `docs/ARCHITECTURE.md` com DA-69
- Atualizar `TODO.md` com progresso DA-69

## Dependências
- `langgraph-checkpoint-postgres` (já instalado)
- `psycopg[binary,pool]` (já instalado)

## Arquivos a modificar
- `app/agent/graph.py`: adicionar checkpointer ao grafo
- `app/config.py`: adicionar flag `checkpointing_enabled`
- `tests/test_checkpointing.py`: testes de checkpointing

## Arquivos a criar
- `docs/DA-69_IMPLEMENTACAO.md`: este arquivo
- `tests/test_checkpointing.py`: testes de checkpointing

## Notas
- O checkpointer é opt-in por flag para não quebrar compatibilidade
- O `thread_id` usa o `incident_id` para garantir unicidade
- O watchdog de timeout continua funcionando (não é cancelamento real)
- Efeitos colaterais devem ser idempotentes (upserts, não inserts)
