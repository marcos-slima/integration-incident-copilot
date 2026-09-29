---
name: verify
description: Build/launch/drive recipe for verifying changes in the Integration Incident Copilot API at its HTTP surface.
---

# Verify — Integration Incident Copilot

## Launch (isolated port, uses host infra from ~/ai-stack)
```bash
uv run uvicorn app.main:app --port 8765 > server.log 2>&1 &
until curl -s -o /dev/null localhost:8765/health; do sleep 1; done
```
- Qdrant `:6333` and Ollama `:11434` must be up (`curl localhost:6333/healthz`, `curl localhost:11434/api/tags`).
- Pass `API_KEY=<value>` in the env to get a known key; otherwise one is generated at startup.

## Flows worth driving
- `GET /health` — liveness only: always 200, no Qdrant/Ollama probes (returns connectors/infra from .env).
- `GET /ready` — readiness: probes Qdrant/Ollama; 503 when a configured service is degraded. Simulate with `OLLAMA_HOST=http://127.0.0.1:1`.
- `POST /diagnose` (header `X-API-Key`) — full LLM run with `qwen3-coder-next:latest` takes ~80s.
  `interface_type` must be one of odata/rfc/servicenow/salesforce/workday/ariba/cap/apim (not `idoc`).
- `POST /events/incident` (header `X-Event-Mesh-Api-Key`), body `{"type":"com.sap.integration.incident.detected.v1","id":"...","data":{...}}`.
- Circuit breaker: isolated Redis (`docker run -d --rm -p 127.0.0.1:6390:6379 redis:7-alpine`), then
  `REDIS_URL=redis://127.0.0.1:6390/0 ODATA_SERVICE_URL=http://127.0.0.1:1/x CONNECTOR_CIRCUIT_FAILURE_THRESHOLD=2 OLLAMA_HOST=http://127.0.0.1:2`;
  calls fail fast (500 from dead Ollama) and `redis-cli hgetall cb:conn:OData` shows the state.
- Alembic migrations: create a throwaway DB in `integration-incident-copilot-postgres-1` (`psql -U iic`), point
  `DATABASE_URL` at it, then run `uv run alembic upgrade 001`, seed rows, and `upgrade head`.
- Redis auth (compose): `REDIS_PASSWORD=x REDIS_HOST_PORT=6391 docker compose -p verifyx --profile async up -d redis`.

## Gotchas
- Don't `pkill -f "uvicorn ..."` from the same shell command — the pattern matches the shell itself. Use `ps | grep [u]vicorn`.
- Use `docker compose -p <name>` to avoid touching the real stack.
