# Deploy in Another Environment (Docker Compose) — Integration Incident Copilot

> English translation of [`docs/DEPLOY.md`](../DEPLOY.md) · source commit `4d18e31` · [Português](../DEPLOY.md)

> Guide to take the project, as it is today, to a new environment (client, server, other machine) via `docker compose`. Covers the real problems found when validating this flow from scratch and how to solve them.

To run locally without Docker (`uv run uvicorn app.main:app --reload`), see [GETTING_STARTED.md](GETTING_STARTED.md).

---

## Prerequisites

| Requirement | Minimum version | Verify |
|---|---|---|
| Docker | 24+ | `docker --version` |
| Docker Compose | v2 | `docker compose version` |
| Node.js | 18+ | `node --version` (only for frontend dev outside Docker; the image builds with `node:22-slim`) |
| uv | any | `uv --version` (ingest and migrations run from host, sections 5-6) |
| Ollama (if local) | any | `ollama --version` |

---

## 1. Clone and configure

```bash
git clone https://github.com/marcos-slima/integration-incident-copilot.git
cd integration-incident-copilot
cp .env.example .env
```

**Required before any `docker compose`:** the `docker-compose.yml`
uses `${VAR:?...}` for `POSTGRES_PASSWORD`, `GRAFANA_PASSWORD` and
`NEO4J_PASSWORD`, and the Compose interpolates all services, even those of
inactive profiles. In `.env.example` the three are commented, so until
`docker compose up -d` fails with `required variable POSTGRES_PASSWORD is
missing a value`. Define them, along with `LLM_CREDENTIALS_MASTER_KEY` — the
compose always injects `DATABASE_URL` in the API, and with it the API does not start without
this key (DA-60):

```bash
for v in POSTGRES_PASSWORD GRAFANA_PASSWORD NEO4J_PASSWORD; do
  echo "$v=$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')" >> .env
done
echo "LLM_CREDENTIALS_MASTER_KEY=$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" >> .env
docker compose config -q   # no output = interpolation ok
```

---

## 2. Frontend build

The `Dockerfile` builds the frontend (React/Vite) from source code in a dedicated stage (`frontend-build`, base `node:22-slim`) and copies the result (`frontend/dist`) to `static/dist` in the final image stage. No manual step: `docker compose build` (or `docker build .`) already builds the frontend alone.

If you want to run the frontend outside Docker (local dev with hot-reload), use `cd frontend && npm install && npm run dev`.

---

## 3. Decide the LLM/Ollama strategy

By default the API uses the Ollama **of the host**: `OLLAMA_HOST=http://host.docker.internal:11434` is fixed in the `x-common-env` of the `docker-compose.yml` (overrides the `.env`). The compose `ollama` service is opt-in (profile `container-ollama`). Two situations:

### A) New environment, without native Ollama installed
Use Ollama in container. It only publishes to `127.0.0.1:11434` of the host, which the `host.docker.internal` (bridge gateway) **does not** reach — so point the API to the service name in the compose network with a `docker-compose.override.yml` (automatically read by Compose):
```yaml
services:
  api:
    environment:
      OLLAMA_HOST: http://ollama:11434
```
(repeat for `worker` if using the `async` profile). Bring up and download models inside the container:
```bash
docker compose --profile container-ollama up -d
docker compose exec ollama ollama pull qwen3.6:35b-a3b
docker compose exec ollama ollama pull nomic-embed-text
```
The ingest of section 6 runs from the host and uses the `OLLAMA_HOST=http://127.0.0.1:11434` of the `.env`, which reaches the published port.

### B) A native Ollama (systemd) is already running on the machine
Common situation in development machines (like the author's study station) and the default path of the compose. If you also bring up the `container-ollama` profile, the symptom is `failed to bind host port 127.0.0.1:11434/tcp: address already in use`.

**Step 1 — do not bring up the `ollama` container (without the `container-ollama` profile):**
```bash
docker compose up -d --no-deps qdrant api
```

**Step 2 — ensure that the `api` container reaches the native Ollama of the host.**
The `docker-compose.yml` already brings this in the `api` service (via `x-common-env`); check if it was not removed:
```yaml
  api:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      OLLAMA_HOST: http://host.docker.internal:11434
```

**Step 3 — ensure that the native Ollama accepts connections coming from the Docker bridge.**
By default, the `ollama.service` (systemd) listens only on `127.0.0.1:11434` — refuses connections coming from containers (`172.17.0.x`). Verify:
```bash
ss -tlnp | grep 11434
```
If it appears `127.0.0.1:11434` (and not `0.0.0.0` or `*:11434`), configure:
```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
echo -e "[Service]\nEnvironment=\"OLLAMA_HOST=0.0.0.0:11434\"" | sudo tee /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

---

## 4. Known bug (fixed in Dockerfile since DA-24): `uv run` in `CMD` tried to resync at startup

Historically the `CMD` of the `Dockerfile` ran `uv run uvicorn ...`, and `uv run` by default tries to resync the environment at each start — including dev dependencies (`pre-commit` → `virtualenv` → `python-discovery`). In a container without network output (or restricted network, like a Kyma cluster with Network Policies), this brought down the container with DNS/timeout error.

**Fixed at the source (DA-24)**: the `Dockerfile` now calls `.venv/bin/uvicorn` directly in the `CMD`, without passing through `uv run` - `docker-compose.yml` and the Kyma manifests (`deploy/kyma/`) no longer need any `command:` override to patch this. If you are using an image built before this phase, your own `command:` override (below) continues to work as workaround:
```yaml
  api:
    command: [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 5. Bring up and validate

```bash
docker compose up -d --no-deps --build qdrant api                # scenario B (native Ollama)
# or
docker compose --profile container-ollama up -d --build          # scenario A (containerized Ollama + override of section 3-A)

curl http://127.0.0.1:8000/health
```

Expected: `{"status":"ok"}`.

If you will use incident persistence (profile `observability`), bring up the
Postgres and apply the migrations from the host — the Alembic reads `DATABASE_URL` from the
`.env`, which on the host points to `127.0.0.1` (the API in the container continues
using the `postgres` injected by the compose):

```bash
docker compose --profile observability up -d postgres grafana
echo "DATABASE_URL=postgresql+asyncpg://iic:$(grep ^POSTGRES_PASSWORD= .env | cut -d= -f2-)@127.0.0.1:5432/iic" >> .env
uv run alembic upgrade head
```

---

## 6. Index the knowledge base

The Qdrant of this compose is a dedicated and empty instance (intentional design — see [ARCHITECTURE.md](ARCHITECTURE.md), Architecture Decision about portability for third-party demos). Populate it:

```bash
QDRANT_URL=http://127.0.0.1:${QDRANT_HOST_PORT:-6333} uv run python -m app.rag.ingest --target incidents
QDRANT_URL=http://127.0.0.1:${QDRANT_HOST_PORT:-6333} uv run python -m app.rag.ingest --target reference
```

Without `--reset`, the ingest processes only documents not yet indexed (idempotent — safe to run again).

---

## 7. End-to-end test

Since DA-18, `/diagnose` always requires authentication: the header `X-API-Key`
or, since DA-54, the session cookie of the UI (login in `POST /auth/login`).
For `curl`, use the key. If you did not configure `API_KEY` in the `.env`, a
random key is generated at each `docker compose up`/restart and notified in
level `WARNING` in the startup log:

```bash
docker logs integration-incident-copilot-api-1 2>&1 | grep "API_KEY"
```

Use this key (or the one you configured in `API_KEY=` in the `.env`, if
you prefer a stable key between restarts):

```bash
curl -s -X POST http://127.0.0.1:8000/diagnose \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <key-from-log-or-.env>" \
  -d '{"description":"IDoc stuck with status 51, mapping error of material"}'
```

Should return a JSON with `probable_root_cause`, `model_confidence`, `diagnosis_confidence`, `next_steps` and `report_markdown`. Without the header (or with wrong value): `401 Unauthorized`; after `AUTH_FAILURES_PER_MINUTE` failures (default 10) from the same IP in the minute, `429 Too Many Requests`.

The same diagnosis is also available via **MCP** (DA-19) in
`POST /mcp/` (with trailing slash - without it, `307 Temporary Redirect`),
reusing the same `X-API-Key`:

```bash
curl -s -X POST http://127.0.0.1:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-API-Key: <key-from-log-or-.env>" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test-client","version":"1.0"}}}'
```

Should return an `message` event with `serverInfo.name == "sap-integration-copilot"` and the tools `diagnose_incident`/`list_connectors` available.

Since **DA-23**, there is also a path of **event-driven
ingestion**: `POST /events/incident` simulates what a subscriber of webhook
of SAP Event Mesh (REST/Webhook push subscription mode) would receive -
fires the same diagnosis automatically, without manual call.
Requires the dedicated header `X-Event-Mesh-Api-Key` (separate key from
`X-API-Key`/`X-A2A-Api-Key` - see startup log or `EVENT_MESH_API_KEY`
in the `.env`):

```bash
curl -s -X POST http://127.0.0.1:8000/events/incident \
  -H "Content-Type: application/json" \
  -H "X-Event-Mesh-Api-Key: <key-from-log-or-.env>" \
  -d '{
        "specversion": "1.0",
        "type": "com.sap.integration.incident.detected.v1",
        "source": "/sap/cpi/monitor",
        "id": "evt-0001",
        "data": {"description": "IDoc stuck with status 51", "interface_type": "rfc"}
      }'
```

The envelope follows strict CloudEvents 1.0: `specversion`, `type`, `source` and `id` are
mandatory, and the pair (`source`, `id`) is the deduplication key — resending the
same event receives 202 twice, but generates **one**
diagnosis. A `type` different from
`com.sap.integration.incident.detected.v1`, or the lack of one of the four
attributes, returns `422 Unprocessable Content`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `port is already allocated` (6333) | Another Qdrant already uses the port (e.g., personal dev stack) | Set `QDRANT_HOST_PORT` in the `.env` (e.g., `QDRANT_HOST_PORT=6335`) — the `docker-compose.yml` already supports via `${QDRANT_HOST_PORT:-6333}` |
| `address already in use` (11434) | Native Ollama already running | See section 3-B |
| `ConnectionError: Failed to connect to Ollama` inside the container | `OLLAMA_HOST` wrong, or native Ollama only on `127.0.0.1` | See section 3-B, steps 2 and 3 |
| Container `api` restarts by itself with DNS/`python-discovery` error | `uv run` trying to sync without network | See section 4 |
| `401 Unauthorized` in `/diagnose` or `/a2a` | Header `X-API-Key`/`X-A2A-Api-Key` missing or wrong (DA-18: key always required, generated automatically if not configured) | See the key generated in the startup log (`docker logs ... \| grep API_KEY`), or configure `API_KEY`/`A2A_API_KEY` in the `.env` |
| `429 Too Many Requests` in `/diagnose`, `/a2a`, `/events/incident` or `/admin` even after fixing the key | The IP missed the key `AUTH_FAILURES_PER_MINUTE` times (default 10) in the 60 s window (`app/auth_guard.py`, SEC-03) | Wait for the minute to pass; behind a proxy, check `FORWARDED_ALLOW_IPS` (see [RATE_LIMITING.md](../RATE_LIMITING.md)) |
| `required variable POSTGRES_PASSWORD is missing a value` (or `GRAFANA_PASSWORD`/`NEO4J_PASSWORD`) in any `docker compose` | Passwords commented in `.env.example`; the Compose interpolates the `:?` of all profiles | See section 1 |
| `DATABASE_URL configured without valid LLM_CREDENTIALS_MASTER_KEY` in API startup | The compose always injects `DATABASE_URL`; the evidence is encrypted (DA-60) | See section 1 |
| `401 Unauthorized` in `/events/incident` | Header `X-Event-Mesh-Api-Key` missing/wrong, or reusing `X-API-Key` by mistake (DA-23: dedicated key, not shared with `/diagnose`/`/a2a`) | See the key generated in the startup log, or configure `EVENT_MESH_API_KEY` in the `.env` |
| `422 Unprocessable Content` in `/events/incident` | Field `type` different from `com.sap.integration.incident.detected.v1` (DA-23: only type recognized today), or missing `specversion`/`id`/`source` (CloudEvents 1.0) | Adjust the payload; the body of the 422 indicates the field (`loc`) missing without echoing the sent value |
| `307 Temporary Redirect` in `POST /mcp` | Missing trailing slash - `app.mount()` of Starlette redirects `/mcp` → `/mcp/` before checking authentication (standard behavior, not MCP bug) | Call `/mcp/` (with trailing slash) directly, or configure the MCP client to follow redirects |
| `RuntimeError: Directory 'static/dist/assets' does not exist` | Frontend not built / not copied to the image | See section 2 |
| `Collection 'sap_incident_docs' doesn't exist` | The compose Qdrant is empty (expected in new environment) | See section 6 |
| `Collection 'sap_reference_library' doesn't exist` (500 in `/diagnose`) | Until this fix, the fallback of `_retrieve_unified` did not catch the 404 (`UnexpectedResponse`) of Qdrant | Fixed in `app/rag/retriever.py` (fallback is ignored with WARNING). In old images: create the empty collection — `curl -X PUT localhost:${QDRANT_HOST_PORT:-6333}/collections/sap_reference_library -H 'Content-Type: application/json' -d '{"vectors":{"size":768,"distance":"Cosine"}}'` |
| `container qdrant/ollama is unhealthy`, log with `exec: "curl": executable file not found` | The official Qdrant and Ollama images do not include `curl`/`wget` | Healthchecks use `/dev/tcp` (Qdrant) and `ollama list` (Ollama) in the `docker-compose.yml` |
| `dependency failed to start: container ...-ollama-1 is unhealthy` | Old stack with `api`/`worker` depending on Ollama in container | The Ollama of the host is the default; the `ollama` container only comes up with `--profile container-ollama` |
| `401` even with `API_KEY` in `.env` | The variable does not reach the container (needs to be in `x-common-env`), or there are duplicate/empty `API_KEY=` lines in the `.env`, or the key was pasted in the previous line (`.env` without final newline) | `docker compose exec api env \| grep ^API_KEY` should show the key; `grep -n ^API_KEY= .env` should show a single line |
| `"/frontend/dist": not found` in build | The Vite generates in `../static/dist` (`outDir`), i.e., `/static/dist` in the `frontend-build` stage | Dockerfile uses `COPY --from=frontend-build /static/dist static/dist` |
| `CONTAINER_DATABASE_URL` with `@localhost` fails inside the container | `localhost` is the container itself. The `DATABASE_URL` of the `.env` does not reach the container: `x-common-env` defines `DATABASE_URL` (environment variable wins over `.env` in Pydantic Settings) | Leave `CONTAINER_DATABASE_URL` empty (uses `@postgres:5432`, with `--profile observability`) or point to a host reachable by the container. There is no way to disable Postgres in container mode: the default always points to `postgres` |
| Variable of `.env` with `$` interpreted as Compose interpolation (warning `variable is not set`) | `$` literal in secret (e.g., `CAP_CLIENT_SECRET`) | Escape as `$$` in the `.env` |

---

*Integration Incident Copilot · github.com/marcos-slima/integration-incident-copilot*
