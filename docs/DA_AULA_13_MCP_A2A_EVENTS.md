# DA-AULA 13: Transporte (MCP, A2A, Events)

## Objetivo da Aula

Compreender as camadas de transporte do Integration Incident Copilot (DA-19/23/32): REST (MCP tools via `/mcp`), JSON-RPC 2.0 (A2A via `/a2a`) e CloudEvents AMQP 1.0 (Event Mesh via `/events/incident`). Ao final, você será capaz de:
- Diferenciar os três protocolos e seus casos de uso
- Documentar o ciclo de vida completo de uma requisição/diagnóstico
- Explicar o papel da autenticidade por chave dedicada (X-API-Key vs X-A2A-Api-Key vs X-Event-Mesh-Api-Key)

---

## 1. Arquitetura de Transporte

### 1.1. Camadas sob o mesmo teto

O copilot expõe **três** superfícies HTTP que chamam a **mesma orquestração** (`run_diagnosis`):
- **REST + MCP tools** (`/mcp`): servidor MCP (DA-19) exposto como ASGI under FastAPI
- **JSON-RPC 2.0 A2A** (`/a2a`): servidor A2A (DA-14) como router do FastAPI
- **CloudEvents AMQP 1.0** (`/events/incident`): webhook (DA-23) como endpoint do FastAPI

```
┌─────────────────────────────────────────────────────────────────────┐
│                         FastAPI app                                 │
├───────────────────────────────┬───────────────┬─────────────────────┤
│  /diagnose                    │  /a2a         │  /events/incident   │
│  (X-API-Key, rate limited)    │  (X-A2A-Key)  │  (X-Event-Mesh-Key) │
│  └─ run_diagnosis()           │  └─ JSON-RPC  │  └─ CloudEvents     │
│                               │     handler   │     webhook         │
│  /mcp                           └─────────────┬─────────────────────┘
│  (streamable HTTP MCP)                        │
│  └─ tool calls → run_diagnosis()              ▼
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2. Por que três?

| Camada | Protocolo | Caso de uso | DA |
|--------|-----------|-------------|-----|
| **REST** | HTTP/2 | Integração direta com agentes externos (Claude Code, Copilot, OpenCode) via ferramentas MCP | DA-19 |
| **JSON-RPC 2.0** | HTTP/JSON | Comunicação entre agentes humanos/agentes (ex: chatbot → copilot via API do Agente A2A) | DA-14 |
| **CloudEvents AMQP 1.0** | HTTP POST (Webhook) | Sistema de monitoração externo (SAP Event Mesh, Solution Manager) detecta falha → copilot diagnose | DA-23/32 |

---

## 2. MCP (DA-19)

### 2.1. Contrato de ferramentas (server-mode)

**Arquivo:** `app/mcp/server.py`

O FastAPI **monta** o servidor MCP como um sub-app ASGI em `/mcp` (via `app.mount()`). O MCP usa **Streamable HTTP Session**, o que significa que:
- Um único endpoint `/mcp` aceita múltiplas requisições em **uma mesma sessão**
- `StreamableHTTPSessionManager.run()` deve ser chamado **uma vez só** por processo (limitação do SDK)

### 2.2. Ferramentas expostas

| Ferramenta | Parâmetros | DA |
|------------|------------|-----|
| `diagnose_incident` | `description`, `logs`, `payload`, `interface_type`, `identifier` | DA-19/27 |
| `list_connectors` | (sem parâmetros) | DA-14/59 |

**Exemplo de requisição MCP (diagnose):**
```json
{
  "id": "12345",
  "method": "tools/call",
  "params": {
    "name": "diagnose_incident",
    "arguments": {
      "description": "Odata timeout ao chamado XPTO",
      "interface_type": "odata",
      "identifier": "XPTO-999"
    }
  }
}
```

**Código que implementa:**
```python
@mcp.tool()
def diagnose_incident(...) -> dict:
    # DA-27: Capability Registry + Agent Execution Policy (fail-closed)
    enforce("diagnose_incident")  # verifica registry + policies
    # ...
    return run_diagnosis(IncidentRequest(**arguments))
```

### 2.3. Autenticação

**Arquivo:** `app/mcp/server.py::RequireApiKeyMiddleware`

Reusa `settings.api_key` (MESMA chave de `/diagnose`, DA-18), via um **middleware ASGI simples**:
- Compara `X-API-Key` com `settings.api_key` usando `secrets.compare_digest`
- Se `X-API-Key` ausente ou inválido → `401 Unauthorized`

**Configuração:** `API_KEY=...` no `.env`

### 2.4. Políticas de execução (DA-27)

**Arquivo:** `app/mcp/policy.py`

- **Capability Registry** (DA-19): lista de ferramentas autorizadas
- **Agent Execution Policy** (DA-27): quando uma ferramenta pode ser chamada por qual agente
- **FAIL-CLOSED** (DA-27): ferramenta sem entrada no registry ou violação de política é negada imediatamente

```python
def enforce(tool_name: str) -> None:
    """DA-27: verifica capability registry + agent policy.
    Fail-closed: ausência de entrada = negação explícita."""
    # ...
```

---

## 3. A2A (DA-14)

### 3.1. Protocolo JSON-RPC 2.0

**Arquivo:** `app/a2a/server.py`

Expõe um router FastAPI em `/a2a`, com dois métodos:
- `message/send` — envia mensagem, executa diagnóstico, retorna task já em estado terminal
- `tasks/get` — consulta task pelo id (útil para clientes que usam polling)

### 3.2. Estrutura de requisição

```json
POST /a2a
{
  "jsonrpc": "2.0",
  "id": "request-001",
  "method": "message/send",
  "params": {
    "message": {
      "text": "Oodata timeout ao chamado XPTO",
      "sender": "client-agent-01",
      "conversation_id": "conv-789"
    }
  }
}
```

**Resposta (sucesso):**
```json
{
  "jsonrpc": "2.0",
  "id": "request-001",
  "result": {
    "task": {
      "id": "task-001",
      "state": "DONE",
      "output": {
        "type": "message",
        "content": {
          "type": "text",
          "text": "Causa provável: timeout no endpoint OData..."
        }
      }
    }
  }
}
```

### 3.3. Autenticação

**Arquivo:** `app/a2a/server.py`

Header dedicado: `X-A2A-Api-Key`, comparado com `settings.a2a_api_key` (ambiente separado de `/diagnose`/`/mcp`).

```python
def _check_auth(x_a2a_api_key: str | None) -> bool:
    """DA-18: settings.a2a_api_key nunca fica vazio após startup."""
    return secrets.compare_digest(x_a2a_api_key or "", settings.a2a_api_key)
```

### 3.4. Task management

**Arquivo:** `app/a2a/task_manager.py` + `task_store.py`

- **Task Store** (Redis ou fallback LRU em memória): persiste tasks entre requisições
- **Task Manager**: orquestra `message/send` e `tasks/get`
- Task sai em estado `DONE` após `run_diagnosis()`, sem streaming (síncrono por escolha de projeto)

---

## 4. Event Mesh (DA-23/32)

### 4.1. Webhook CloudEvents

**Arquivo:** `app/events/consumer.py` + `app/main.py::incident_event_webhook`

Recebe envelope **CloudEvents** (formato REST/Webhook push subscription do SAP Event Mesh):
```json
{
  "specversion": "1.0",
  "type": "com.sap.integration.incident.detected.v1",
  "source": "/sap/cpi/flows/XPTO",
  "id": "evt-20241002-12345",
  "time": "2026-10-02T12:00:00Z",
  "data": {
    "description": "IDoc status 51 detectado",
    "interface_type": "rfc",
    "identifier": "IDOC-51-DEMO",
    "logs": "..."
  }
}
```

### 4.2. Assincronicidade (202 Accepted + BackgroundTasks)

**Arquivo:** `app/main.py::incident_event_webhook` (l.520-561)

Para evitar timeouts no Event Mesh (timeout padrão ~30s), resposta **202 Accepted** imediata:
```python
@app.post("/events/incident", status_code=202, ...)
def incident_event_webhook(envelope: IncidentEventEnvelope, background_tasks: BackgroundTasks) -> Response:
    body = handle_incident_event_async(envelope, background_tasks)
    # 202 vindo antes do LLM terminar
    return JSONResponse(status_code=202, content=body)
```

**Código que processa:**
```python
def handle_incident_event_async(
    envelope: IncidentEventEnvelope, background_tasks: BackgroundTasks
) -> dict:
    if settings.redis_url:
        # Com Redis: RQ fila durable com DLQ
        job_id = enqueue_incident_event(envelope.model_dump(mode="json"))
        return {"status": "queued", "job_id": job_id}

    # Sem Redis: BackgroundTasks no próprio processo (não durable)
    background_tasks.add_task(_run_diagnosis_background, envelope)
    return {"status": "accepted", "job_id": None}
```

### 4.3. Idempotência

**Arquivo:** `app/events/idempotency.py`

SAP Event Mesh usa **at-least-once delivery** (pode reenviar mesmo evento em caso de timeout/faixa de rede). Logo:
- `cloudEvents.id` usado como chave idempotência
- `idempotency.is_duplicate()` verifica se o mesmo `id` já foi processado
- Se duplicado → resposta `{"status": "duplicate", "job_id": None}` (202 ou 200, dependendo do modo)

### 4.4. DLQ (Dead Letter Queue)

**Arquivo:** `app/events/consumer.py::handle_incident_event_async`

Exceções durante diagnóstico em background são **logadas com nível ERROR** (não silenciadas):
```python
def _run_diagnosis_background(envelope: IncidentEventEnvelope) -> None:
    try:
        result = run_diagnosis(to_incident_request(envelope))
        idempotency.mark_completed(event_id)
    except Exception:
        idempotency.release(event_id)  # libera id, para reprocessamento ser aceito
        _logger.exception(
            "[events] Falha no diagnostico em background (DLQ) — "
            "cloudevents.source=%s cloudevents.id=%s",
            ...,
        )
```

### 4.5. AMQP 1.0 (DA-32/40)

**Arquivo:** `app/events/amqp_consumer.py`

Migração de **aiormq (AMQP 0.9.1)** → **python-qpid-proton (AMQP 1.0)**.

**Problema original (DA-32):** aiormq implementa AMQP 0.9.1. Solace Cloud usa exclusivamente AMQP 1.0. Conexão parecia funcionar, mas operações falhavam silenciosamente (frames 0.9.1 inválidos para broker 1.0).

**Solução (DA-40):** python-qpid-proton com wrapper `asyncio.run_in_executor()` — API qpid-proton é síncrona/blocking, roda em thread pool sem bloquear event loop.

**Configuração (via `.env`):**
```bash
AMQP_ENABLED=true
AMQP_HOST=mr-connection-kytjcnxk2he.messaging.solace.cloud
AMQP_PORT=5671
AMQP_USERNAME=solace-cloud-client
AMQP_PASSWORD=<secret>
AMQP_QUEUE=integration/incidents
AMQP_PREFETCH=1
AMQP_RECONNECT_DELAY=5
```

---

## 5. Autenticação por chave dedicada (DA-18/19/23/27/54)

### 5.1. Superfícies e chaves

| Superfície | Header | Variável `.env` | DA |
|------------|--------|-----------------|-----|
| `/diagnose`, `/mcp` | `X-API-Key` | `API_KEY` | DA-18/19 |
| `/a2a` | `X-A2A-Api-Key` | `A2A_API_KEY` | DA-14/18 |
| `/events/incident` | `X-Event-Mesh-Api-Key` | `EVENT_MESH_API_KEY` | DA-23 |

### 5.2. Por que chaves dedicadas?

- **Separação de contexto:** webhook (sistema de monitoração) não pode reutilizar credencial de chatbot (risco de vazamento cruzado)
- **Revogação granular:** se uma chave vaza, revoga só ela, não todas as channels
- **Auditoria:** logs podem separar requisições por canal (ex: `X-Event-Mesh-Api-Key` ausente → não veio da Event Mesh)

### 5.3. Startup e geração aleatória

**Arquivo:** `app/main.py::_ensure_api_keys_configured()`

Se `API_KEY` / `A2A_API_KEY` / `EVENT_MESH_API_KEY` não estão configurados no `.env`, o `lifespan` gera uma chave aleatória e avisa no log:

```python
def _ensure_api_keys_configured() -> None:
    if not settings.api_key:
        key = secrets.token_urlsafe(32)
        logger.warning(f"API_KEY ausente, gerando uma临时 (DA-18): {key}")
        settings.api_key = key
    # ...
```

**Observação:** Chave gerada **aleatoriamente no startup** (não persistida) — para desenvolvimento. Para produção, **sempre configurar chave estática** no `.env`.

---

## 6. Idempotência (DA-23/32/41)

### 6.1. Redis Set (persistido)

**Arquivo:** `app/events/idempotency.py` (modo com Redis)

- **Key prefix:** `idempotency:` + `{cloudEvent.id}`
- **Valor:** `1` (não importa, Set só guarda a presença)
- **Expiry:** mesmo TTL que RQ (default 1 dia)

```python
def mark_completed(event_id: str | None) -> None:
    """DA-41: Redis Set compartilhado entre pods."""
    if not event_id or not settings.redis_url:
        return
    redis_client.setex(f"idempotency:{event_id}", ttl, 1)


def is_duplicate(event_id: str | None) -> bool:
    return redis_client.exists(f"idempotency:{event_id}") > 0
```

### 6.2. Fallback LRU em memória

**Arquivo:** `app/events/idempotency.py` (modo sem Redis)

- **LRU cache** de eventos processados (não persistido, perde em restart)
- TTL simulado: evento é liberado após `ttl_seconds` (default 1 dia)

```python
# LRU cache de event_id → timestamp de processamento
_cache: dict[str, float] = {}


def is_duplicate(event_id: str | None) -> bool:
    if not event_id:
        return False
    now = time.time()
    # 清理 expirados
    _cache.clear()
    # ...
```

**Warning (DA-41):** fallback LRU é **não-durable** — em restart, eventos antigos perdem o estado de "já processado". Aceitável para desenvolvimento/local, inadequado para produção.

---

## 7. Rate Limiting

### 7.1. Políticas por camada

| Camada | Rate limit | DA |
|--------|------------|-----|
| `/diagnose`, `/a2a`, `/events/incident` | 10/min por IP | DA-32/35 |

### 7.2. Implementação ( SlowAPI)

**Arquivo:** `app/rate_limit.py` + `app/main.py`

```python
limiter = SlowAPI()

@app.post("/diagnose", ...)
@limiter.limit("10/minute")
def diagnose(...) -> ...:
    ...

@app.post("/events/incident", ...)
@limiter.limit("10/minute")
def incident_event_webhook(...) -> ...:
    ...
```

---

## 8. Exercícios Práticos

### 8.1. Escolha da camada

Para cada cenário abaixo, escolha a camada correta (REST/MCP, A2A, Events) e justifique:
1. **Agentes externos chamam o copilot** via API para fazer diagnóstico em background (ex: chatbot → copilot)
2. **SAP Solution Manager detecta IDoc status 51** e dispara um webhook para o copilot
3. **Claude Code (agente humano) usa o copilot como ferramenta** (MCP tool call)

**Solução:**
- **1)** **A2A** (JSON-RPC 2.0) — comunicação entre agentes, requisições sincronas com estado task
- **2)** **Events (CloudEvents)** — sistema de monitoração externo (não humano), webhook, at-least-once delivery
- **3)** **MCP (REST)** — ferramentas expostas via Streamable HTTP (DA-19)

### 8.2. Idempotência e reentrega

Cenário: Event Mesh envia mesmo `cloudEvents.id` duas vezes (timeout na primeira entrega). O que acontece?

**Resposta:**
1. Primeira chamada → `handle_incident_event_async` → `is_duplicate()` → `False` → `background_tasks.add_task()` → `mark_completed()`
2. Segunda chamada → `is_duplicate()` → `True` → resposta `{"status": "duplicate", "job_id": None}` (sem processo em background)
3. Se o segundo envelope tiver **exceção em background**, `idempotency.release(event_id)` é chamada → reentrega com mesmo `id` é aceita (fica com a chance de sucesso ao invés de erro perpetuado).

### 8.3. Erro comum: `app.mount()` não propaga lifespan

**Problema (documentado em `app/main.py`, l.237-246):** `starlette.Mount()` **não propaga lifespan ASGI** para sub-apps automaticamente. O `mcp_server.session_manager.run()` (que deve rodar UMA vez por processo) **não é disparado** se o `with` não estiver no lifespan do FastAPI.

**Solução (DA-19):** Manter `async with mcp_server.session_manager.run()` dentro do lifespan do FastAPI (l.247), mesmo `app.mount()` sem `lifespan`.

---

## 9. Invariantes Críticas

1. **DA-14:** A2A e `/diagnose` usam a **mesma orquestração** (`run_diagnosis`) — não há lógica duplicada de diagnóstico
2. **DA-18:** Autenticação **NUNCA** fica desabilitada — chaves aleatórias geradas no startup, ou fixas no `.env`
3. **DA-19:** MCP serve ferramentas via **Streamable HTTP Session**, não stdio — um único endpoint `/mcp` serve múltiplas chamadas em uma sessão
4. **DA-27:** Capability Registry é **FAIL-CLOSED** — ferramenta sem entrada no registry é negada imediatamente
5. **DA-23/32:** Idempotência por `cloudEvents.id` é **obrigatória** —Event Mesh reenvia eventos em caso de timeout
6. **DA-41:** Redis Set ou fallback LRU — Falha no Redis não quebra o endpoint (falla-graciosa, não exception)

---

## 10. Limitações Conhecidas

| Limitação | Detalhe |
|-----------|---------|
| MCP SessionManager é single-tone | Só pode haver **UM** TestClient disparando o lifespan (DA-19, test guide) |
| A2A é síncrono (não stream) | Task sai em estado `DONE` após `run_diagnosis()`, sem streaming (escolha de projeto) |
| Event Mesh webhook é HTTP/1.1 | Não usa HTTP/2, chunked encoding ou push server-sent events |
| LRU fallback de idempotência é não-durable | Em restart, eventos antigos perdem o estado "já processado" (aceitável para dev, não prod) |

---

## 11. Referências Rápidas

### 11.1. Arquivos-Chave

| Arquivo | Responsabilidade | DAs |
|---------|-----------------|-----|
| `app/mcp/server.py` | Servidor MCP (Streamable HTTP Session), tools `diagnose_incident`, `list_connectors` | DA-19/27 |
| `app/mcp/policy.py` | Capability Registry + Agent Execution Policy (FAIL-CLOSED) | DA-27 |
| `app/a2a/server.py` | Servidor JSON-RPC 2.0 (`message/send`, `tasks/get`) | DA-14 |
| `app/a2a/task_manager.py` | Orquestra A2A tasks, persiste em Redis/LRU | DA-14/41 |
| `app/events/consumer.py` | Webhook CloudEvents → `run_diagnosis()` (202 Accepted, BackgroundTasks) | DA-23 |
| `app/events/idempotency.py` | Idempotência por `cloudEvents.id` (Redis Set / fallback LRU) | DA-23/32/41 |
| `app/events/amqp_consumer.py` | Consumidor AMQP 1.0 (SAP Event Mesh via Solace Cloud) | DA-32/40 |
| `app/main.py` | FastAPI app, monta `/mcp` via `app.mount()`, `/a2a` via router, `/events/incident` | DA-14/19/23/32 |

### 11.2. DAs Relacionadas

| DA | Resumo |
|----|--------|
| DA-14 | A2A JSON-RPC 2.0 servidor (mesma orquestração `/diagnose`) |
| DA-18 | Autenticação por chave obrigatória em `/diagnose`, `/a2a`, `/mcp` |
| DA-19 | Servidor MCP (Streamable HTTP Session, capability catalog) |
| DA-23 | Webhook CloudEvents (Event Mesh) → `run_diagnosis()` (202 Accepted + BackgroundTasks) |
| DA-27 | Capability Registry FAIL-CLOSED (tool call policy) |
| DA-32 | AMQP 1.0 via Solace Cloud (migração aiormq → python-qpid-proton) |
| DA-40 | Migração AMQP 0.9.1 → 1.0 (aiormq → qpid-proton) |
| DA-41 | Circuit breaker com Redis compartilhado (fallback LRU em memória) |

### 11.3. Endpoints

| Endpoint | Método | Autenticação | Status code principal | DA |
|----------|--------|--------------|----------------------|-----|
| `/mcp` | `POST` | `X-API-Key` | 200 (tool call response) | DA-19/27 |
| `/a2a` | `POST` | `X-A2A-Api-Key` | 200 (JSON-RPC result/error) | DA-14/18 |
| `/events/incident` | `POST` | `X-Event-Mesh-Api-Key` | 202 Accepted (webhook) | DA-23/32 |

---

**Fim da trilha de agentes:** `DA_AULA_13_MCP_A2A_EVENTS.md` completa os três grandes tópicos restantes (MCP, A2A, Events). O aluno agora domina por completo o pipeline `supervisor → connector → retrieve → diagnose → report`, as camadas determinísticas (Rule Engine, Escalation), e o transporte REST/MCP/A2A/Events que expõe o prodto.
