# Getting Started — Integration Incident Copilot

> English translation of [`docs/GETTING_STARTED.md`](../GETTING_STARTED.md) · source commit `60bc8d3` · [Português](../GETTING_STARTED.md)
> If this page and the Portuguese source differ, the code and the Portuguese source prevail.

> From zero to first diagnosis in less than 10 minutes.

---

## Prerequisites

| Requirement | Minimum version | Verify |
|---|---|---|
| Python | 3.12+ | `python3 --version` |
| uv | >= 0.12.17 | `uv --version` |
| Ollama | any | `ollama --version` |
| Docker (optional) | 24+ | `docker --version` |

---

## 1. Clone and configure the environment

```bash
git clone https://github.com/marcos-slima/integration-incident-copilot.git
cd integration-incident-copilot
cp .env.example .env
uv sync
```

### Passwords required by compose

The `docker-compose.yml` uses `${VAR:-REQUIRED_SET_IN_ENV}` for
`POSTGRES_PASSWORD`, `GRAFANA_PASSWORD`, `GRAFANA_DB_PASSWORD` and
`NEO4J_PASSWORD`. Compose interpolates **all** services, even those of inactive
profiles, so interpolation never fails — but the sentinel `REQUIRED_SET_IN_ENV`
is public: Postgres, Grafana and Neo4j **refuse to start** with it, and the API
refuses a `DATABASE_URL`/`NEO4J_PASSWORD` that contains it (IIC-AUD-403).
`.env.example` leaves these variables empty/commented on purpose. Generate the
passwords before starting those profiles.

Also generate `LLM_CREDENTIALS_MASTER_KEY` (Fernet key): with `DATABASE_URL`
configured the API **does not start** without it (DA-60). In container mode
persistence is opt-in: the API only receives `DATABASE_URL` when
`CONTAINER_DATABASE_URL` is set (IIC-AUD-405, see step 5b).

```bash
for v in POSTGRES_PASSWORD GRAFANA_PASSWORD NEO4J_PASSWORD; do
  echo "$v=$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')" >> .env
done
echo "LLM_CREDENTIALS_MASTER_KEY=$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" >> .env
docker compose config -q   # no output = interpolation ok
```

---

## 2. Pull the models

```bash
ollama pull qwen3.6:35b-a3b
ollama pull nomic-embed-text
```

---

## 3. Start the infrastructure

The infrastructure comes from the `docker-compose.yml` **of this repository** — not from
any external directory. Without profile, only `api` and `qdrant` start; the rest is
opt-in:

| Profile | Services |
|---|---|
| (none) | `api`, `qdrant` |
| `observability` | `postgres`, `grafana`, `reporter` |
| `async` | `redis`, `worker` (only have effect with `REDIS_URL` in `.env`) |
| `graphrag` | `neo4j` |
| `container-ollama` | `ollama` |
| `mailpit` / `messagepit` | `mailpit` / `messagepit` |

```bash
# Qdrant (required) + Postgres/Grafana (incident persistence, optional)
docker compose --profile observability up -d qdrant postgres grafana

# GraphRAG (opt-in; without this the app runs normally, just without graph)
docker compose --profile graphrag up -d neo4j
```

Starts: Qdrant (`localhost:6333`), Postgres (`localhost:5432`), Neo4j
(`localhost:7474`, only with `graphrag` profile), Grafana (`localhost:3002`).

With Postgres, apply migrations before running the application. Alembic reads
`DATABASE_URL` from `.env` — use the same password generated in step 1:

```bash
echo "DATABASE_URL=postgresql+asyncpg://iic:$(grep ^POSTGRES_PASSWORD= .env | cut -d= -f2-)@127.0.0.1:5432/iic" >> .env
uv run alembic upgrade head
```

---

## 4. Index the knowledge base

```bash
uv run python -m app.rag.ingest --target incidents --reset
```

The primary root of the incident base is `data/knowledge_base/` (recursive).
While it does not exist or has no `.md`/`.pdf`/`.epub`, the ingest
uses the fallback `data/sample_docs/` (the example cases of the repository) —
this is what happens in a new clone. The agent queries this local base, not the
internet (web search is an opt-in fallback, `WEB_SEARCH_ENABLED`).

---

## 5. Choose how to run the application

The two routes use the **same** infrastructure from step 3. What changes is
only where the Python process runs — and the `.env` needs to reflect this.

### 5a. Native, for step-by-step debugging (recommended for study)

```bash
uv run uvicorn app.main:app --reload
```

Access http://localhost:8000 and use the breakpoints of
`.vscode/launch.json` ("Debug: FastAPI (uvicorn)") — the code runs on your
machine, with the VS Code debugger attached.

Requires in `.env` the hosts **of your machine**, never service names:

```bash
QDRANT_URL=http://127.0.0.1:6333
DATABASE_URL=postgresql+asyncpg://iic:PASSWORD@127.0.0.1:5432/iic
NEO4J_URI=bolt://127.0.0.1:7687     # only if GRAPH_RAG_ENABLED=true
```

### 5b. In container, for deployment

```bash
docker compose --profile observability --profile graphrag up -d
```

First set in `.env` the database URL **as the container sees it**
(persistence in container mode is opt-in, IIC-AUD-405):

```bash
echo "CONTAINER_DATABASE_URL=postgresql+asyncpg://iic:$(grep ^POSTGRES_PASSWORD= .env | cut -d= -f2-)@postgres:5432/iic" >> .env
```

Access http://localhost:8000. Here the `.env` **does not** define the hosts: the
`x-common-env` of `docker-compose.yml` injects `QDRANT_URL` and `NEO4J_URI` with
the internal names (`qdrant`, `neo4j`) and `DATABASE_URL` from
`CONTAINER_DATABASE_URL`, as environment variables, which take precedence over
the `.env` mounted in the container. The container does not see the host
loopback, so `127.0.0.1` only works in native mode.

The Ollama is from the host, via `OLLAMA_HOST=http://host.docker.internal:11434`
(fixed in `x-common-env`). This only works if the native Ollama listens on
`0.0.0.0` — by default the systemd service listens only on `127.0.0.1` and refuses
the container. See [DEPLOY.md](../DEPLOY.md), section 3-B, step 3 (Portuguese only).

If you need a Postgres **external** to the compose (RDS, Cloud SQL, BTP),
define `CONTAINER_DATABASE_URL` instead of `DATABASE_URL`; the same for
`CONTAINER_NEO4J_URI`.

---

## 6. How to expand the knowledge base

The agent only knows what you teach. Each document in `data/knowledge_base/`
is a troubleshooting case that the agent can retrieve and use in diagnosis.

> When creating the first file in `data/knowledge_base/`, the fallback
> `data/sample_docs/` stops being read. To keep the example cases,
> copy them together: `mkdir -p data/knowledge_base && cp data/sample_docs/*.md data/knowledge_base/`.

### Document structure

```markdown
# [Problem Title] — [System]

## Symptom
What the user observes: error code, SAP transaction, behavior.

## Common causes
- Most frequent cause
- Second most common cause

## Diagnosis
1. First investigation step
2. Second step

## Typical resolution
Concrete action to resolve. Include SAP transactions and validation criterion.
```

### Example — new document

Create the file inside `data/knowledge_base/` (primary root of the base):

```markdown
# Authorization Failure in BAPI — SAP ABAP

## Symptom
RFC call to a BAPI returns AUTHORIZATION_FAILURE.
The technical user has RFC access in SM59, but the BAPI fails.

## Common causes
- RFC user without specific BAPI authorization object
- Role of RFC user does not contain the equivalent transaction
- User blocked by invalid login attempts (SU01)

## Diagnosis
1. Execute the BAPI via SE37 with the RFC user in debug mode
2. Check the authorization log via SU53 after the failure
3. Check user blocking via SU01

## Typical resolution
Add the SU53 authorization object to the RFC user role
via PFCG. After adjustment, test again via SE37.
```

After creating the file, reindex:

```bash
uv run python -m app.rag.ingest --target incidents --reset
```

From the next diagnosis, the agent considers the new case.

### Best practices

- Use exact technical terms: RFC_COMM_FAILURE, SM59, BD87, status 51
- Describe the symptom as the user would describe when opening a ticket
- One document per problem — do not group unrelated problems
- Supported formats: .md (recommended), .pdf and .epub

---

## 7. Real connectors

By default all connectors operate in mock mode.
To connect to real systems, configure in `.env`:

### OData / SAP Gateway
```
ODATA_SERVICE_URL=https://system.sap.com/sap/opu/odata/sap/API_SALES_ORDER_SRV
ODATA_OAUTH_TOKEN_URL=https://xsuaa.authentication.sap.hana.ondemand.com/oauth/token
ODATA_CLIENT_ID=client-id
ODATA_CLIENT_SECRET=secret
```

### RFC / SAP ABAP
```
SAP_ASHOST=system.sap.com
SAP_SYSNR=00
SAP_CLIENT=100
SAP_USER=RFC_USER
SAP_PASSWORD=password
LD_LIBRARY_PATH=/usr/local/sap/nwrfcsdk/lib
```

### ServiceNow
```
SERVICENOW_INSTANCE_URL=https://instance.service-now.com
SERVICENOW_USERNAME=admin
SERVICENOW_PASSWORD=password
```

---

## 8. Alternative LLM providers

Before changing the provider, four prerequisites:

1. **Dependency.** `langchain-openai` is the `openai` extra (also valid for
   Azure). In native mode: `uv sync --extra openai`. The Docker image already includes it.
2. **Data sovereignty.** The default is `DATA_SOVEREIGNTY_MODE=strict` with
   `SENSITIVITY_DEFAULT=confidential`: every incident is confidential and only goes
   to local provider — the gateway **denies** the cloud. To release, the two
   variables together (fail-closed), with the real destination ORIGIN:
   ```bash
   DATA_SOVEREIGNTY_MODE=cloud_with_dlp
   CONFIDENTIAL_ALLOWED_ORIGINS=https://api.openai.com   # or https://openrouter.ai, https://recurso.openai.azure.com
   ```
   In a demo without sensitive data, `SENSITIVITY_DEFAULT=public` also releases
   (only for incident without real connector data nor declared PII). Matrix in
   effect: `GET /llm/policy`.
3. **Container mode.** `LLM_PROVIDER` and `OLLAMA_HOST` are fixed in
   `x-common-env` of `docker-compose.yml` and override the `.env` — changing the
   `.env` does not change the provider. Create a `docker-compose.override.yml` (the
   Compose reads it automatically) and keep credentials in `.env`:
   ```yaml
   services:
     api:
       environment:
         LLM_PROVIDER: openai   # or azure_openai
   ```
   Repeat the block for `worker` if using the `async` profile. Then:
   `docker compose up -d api`.
4. **Embeddings without GPU.** Without Ollama, embeddings also need
   another source: `EMBEDDING_BACKEND=fastembed` (in process). Changing the backend
   changes the vector space — recreate the collection with `--reset-collection` (step 4).

```bash
# OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o

# Azure OpenAI
LLM_PROVIDER=azure_openai
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://recurso.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=gpt-4o

# OpenRouter (DA-92 canonical model via API, no local GPU; check the slug in the OpenRouter catalog)
LLM_PROVIDER=openai
OPENAI_API_KEY=openrouter-key
OPENAI_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=<openrouter-model-slug>
EMBEDDING_BACKEND=fastembed
```

---

## Troubleshooting

**libsapnwrfc.so not found**
```bash
export LD_LIBRARY_PATH=/usr/local/sap/nwrfcsdk/lib:$LD_LIBRARY_PATH
```

**`REQUIRED_SET_IN_ENV` in an authentication error** (Postgres/Grafana/Neo4j)
Missing passwords in `.env` — compose used the sentinel. See step 1,
"Passwords required by compose".

**Qdrant connection refused**
```bash
docker compose up -d qdrant
```

**`could not translate host name "postgres"` (container mode)**
The `postgres` service is not up. In container mode the `DATABASE_URL` of
the `.env` is not used: `x-common-env` injects `DATABASE_URL` from
`CONTAINER_DATABASE_URL`, and the environment variable takes precedence over
the `.env`. Start the database with
`docker compose --profile observability up -d postgres`, or point
`CONTAINER_DATABASE_URL` to an external Postgres (see step 5b).

**Native Postgres refuses connection**
The `.env` needs `127.0.0.1:5432`, not `postgres:5432` — `postgres` is the
service name inside the compose network and does not resolve on the machine.

**Model not found**
```bash
ollama pull qwen3.6:35b-a3b && ollama pull nomic-embed-text
```

**Confidence always low**
```bash
uv run python -m app.rag.ingest --target incidents --reset
```

---

*Integration Incident Copilot · github.com/marcos-slima/integration-incident-copilot*
