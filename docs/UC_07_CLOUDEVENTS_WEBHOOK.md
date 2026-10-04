# Use Case 7: CloudEvents Webhook (Event Mesh)

**Contexto:** Webhook do ServiceNow ou SAP Event Mesh dispara diagnóstico via evento

## Fluxo (DA-23)

### 1. Endpoint (app/events/consumer.py)

```python
# app/events/consumer.py::receive_cloudevent()
@router.post("/events/webhook")
async def receive_cloudevent(cloudevent: CloudEvent):
    # Parse CloudEvent (CloudEvents SDK)
    event_id = cloudevent.id
    event_source = cloudevent.source
    event_type = cloudevent.type  # "com.sap.iic.incident"

    # Extract incident data from cloudevent.data
    data = cloudevent.data
    request = IncidentRequest(
        description=data.get("description", ""),
        interface_type=data.get("interface_type"),
        identifier=data.get("identifier"),
        logs=data.get("logs", []),
        payload=data.get("payload", {}),
    )

    # Processa diagnóstico
    response = run_diagnosis(request, debug=False, llm_model=None)

    # Return response (opcional)
    return {
        "incident_id": response.incident_id,
        "status": "processed",
        "latency_ms": response.latency_ms,
    }
```

### 2. Example CloudEvent Body

```json
{
  "specversion": "1.0",
  "type": "com.sap.iic.incident",
  "source": "https://sap.com/iic/webhook",
  "id": "evt-12345",
  "time": "2026-10-02T10:15:30Z",
  "datacontenttype": "application/json",
  "data": {
    "description": "HTTP 500 ao chamar BAPI_MATERIAL_SAVEDATA",
    "interface_type": "odata",
    "identifier": "MAT-789",
    "logs": ["BAPI return code: 4", "RFC error: COMMUNICATION_FAILURE"],
    "payload": {
      "material": "MAT-789",
      "plant": "1000"
    }
  }
}
```

### 3. Event Mesh Integration (DA-32/40)

**Protocolo:** AMQP 1.0 (qpid-proton, não aiormq)

```python
# app/events/amqp_consumer.py::AMQPConsumer
class AMQPConsumer:
    def __init__(self, connection_url: str, queue_name: str):
        self.connection = Connection(connection_url)  # AMQP 1.0
        self.session = self.connection.session()
        self.receiver = self.session.receiver(queue_name)

    def consume(self):
        while True:
            message = self.receiver.receive()
            cloudevent = parse_cloudevent(message.body)
            await receive_cloudevent(cloudevent)
            message.accept()
```

### 4. Workflow Completo

1. **ServiceNow** → POST webhook `/events/webhook`
2. **FastAPI** → `receive_cloudevent()` → `run_diagnosis()`
3. **LangGraph** → supervisor → connector → retrieve → diagnose → report
4. **Response** → HTTP 200 + `{"incident_id": "xxx"}`
5. **Best-effort persistence** → PostgreSQL `incidents` table

### 5. Solace Cloud Integration (DA-32)

**Topico:** `iic/incidents`

```
ServiceNow → webhook → /events/webhook → run_diagnosis
                          ↓
                    AMQP 1.0 → Solace Cloud → replay
```

**Vantagem:** Mensagens persistidas, replay possível, QoS garantido

### 6. DA-40: Migration aiormq → qpid-proton

**Problema:** aiormq (AMQP 0.9.1) não suporta features necessárias de Solace

**Solução:** qpid-proton (AMQP 1.0) com wrapper asyncio

```python
# app/events/amqp_consumer.py (DA-40)
from qpid_proton import Connection, Session, Receiver


class AMQPConsumer:
    async def connect(self):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._connect_sync)

    def _connect_sync(self):
        conn = Connection()  # AMQP 1.0
        conn.open()
        session = conn.session()
        receiver = session.receiver(self.queue)
        receiver.open()
        return receiver
```

### 7. Observabilidade

**Langfuse Context:**
- `trace_id` gerado por `run_diagnosis()`
- `span_id` por node (supervisor, connector, etc.)
- Evento webhook logger: `event_id` grava nos metadata

**Exemplo:**
```json
{
  "trace_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "event_id": "evt-12345",
  "provider": "ollama",
  "latency_ms": 2500
}
```

### 8. Idempotência

**Pattern:** CloudEvent `id` único por evento

```python
# app/events/consumer.py
@router.post("/events/webhook")
async def receive_cloudevent(cloudevent: CloudEvent):
    # Check if already processed
    existing = await db.fetchrow(
        "SELECT id FROM cloudevent_processed WHERE event_id = $1", cloudevent.id
    )
    if existing:
        return {"status": "already_processed", "incident_id": existing["incident_id"]}

    # Process
    response = run_diagnosis(request)
    await db.execute(
        "INSERT INTO cloudevent_processed (event_id, incident_id, timestamp) VALUES ($1, $2, NOW())",
        cloudevent.id,
        response.incident_id,
    )
    return response
```

**Benefício:** Re-play do webhook não gera duplicate incidents
