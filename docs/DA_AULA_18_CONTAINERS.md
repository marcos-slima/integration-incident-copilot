# DA_AULA_18_CONTAINERS.md — Build & Container Deployment (Módulo 18)

## Objetivo

Documentar o build e deployment de container do Integration Incident Copilot:
- Dockerfile multi-stage (DA-24)
- Docker Compose com perfis
- Script de startup (`scripts/start-docker.sh`)
- Variáveis de ambiente e secrets
- Healthchecks e restart policies

---

## Arquitetura

### Dockerfile Multi-Stage (DA-24)

**Stage 1: Frontend (node:22-slim)**
```dockerfile
FROM node:22-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
# Output: /frontend/static/dist/
```

**Stage 2: App (python:3.12-slim)**
```dockerfile
FROM python:3.12-slim AS app
WORKDIR /app
RUN apt-get update && apt-get install -y gcc libc6-dev ... && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra reports
COPY app/ scripts/ data/sample_docs/ static/
```

**Stage 3: Final (app + frontend compilado)**
```dockerfile
FROM app AS final
COPY --from=frontend-build /static/dist static/dist
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1
CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Correções DA-24:**
1. `uv.lock` + `--frozen` → builds reproduzíveis
2. Não-root user (`appuser`, uid 1000) → compliant com PodSecurityStandards restricted
3. CMD direto (`.venv/bin/uvicorn`) → evita `uv run` ressincronizando no startup (DA-24)
4. Healthcheck explícito (sem `curl`/`wget`, usa stdlib `urllib`) → orquestradores detectam health real

### Docker Compose (serviços)

**serviços principais:**
| Serviço | Imagem | Perfil | Funcionalidade |
|---|---|---|---|
| `api` | `.` (multi-stage final) | default | FastAPI + frontend estático |
| `worker` | `.` (target: app) | `async` | RQ worker (fila de diagnóstico assíncrono) |
| `qdrant` | `qdrant/qdrant:v1.19.0` | default | Vector DB (RAG) |
| `ollama` | `ollama/ollama:0.34.1` | `container-ollama` | LLM provider (se não usar host) |
| `postgres` | `postgres:16-alpine` | `observability` | Persistência de incidentes |
| `grafana` | `grafana/grafana:11.3.0` | `observability` | Dashboards |
| `neo4j` | `neo4j:5.26.0-community` | `graphrag` | GraphRAG |
| `redis` | `redis:7.4-alpine` | `async` | Fila RQ e task store A2A |

**`x-common-env` (shared environment):**
```yaml
x-common-env: &common-env
  LLM_PROVIDER: ollama
  OLLAMA_HOST: http://host.docker.internal:11434
  LLM_MODEL: ${LLM_MODEL:-qwen3-coder-next:latest}
  EMBEDDING_MODEL: ${EMBEDDING_MODEL:-nomic-embed-text}
  QDRANT_URL: http://qdrant:6333
  DATABASE_URL: ${CONTAINER_DATABASE_URL:-postgresql+asyncpg://${POSTGRES_USER:-iic}:${POSTGRES_PASSWORD:-REQUIRED_SET_IN_ENV}@postgres:5432/${POSTGRES_DB:-iic}}
  NEO4J_URI: ${CONTAINER_NEO4J_URI:-bolt://neo4j:7687}
  NEO4J_USER: ${NEO4J_USER:-neo4j}
  NEO4J_PASSWORD: ${NEO4J_PASSWORD:-}
  API_KEY: ${API_KEY:-}
  A2A_API_KEY: ${A2A_API_KEY:-}
  WEB_UI_USERS: ${WEB_UI_USERS:-}
  SESSION_SECRET: ${SESSION_SECRET:-}
  SESSION_TTL_HOURS: ${SESSION_TTL_HOURS:-8}
  ADMIN_API_KEY: ${ADMIN_API_KEY:-}
```

### Script de Startup (`scripts/start-docker.sh`)

**Functionality:**
1. Check pré-requisitos (`docker`, `ollama` (se LLM_PROVIDER=ollama), `.env`)
2. Start Ollama (se presente no host, via `nohup ollama serve`)
3. Start infra (ai-stack: Qdrant, Neo4j, Langfuse)
4. Start observabilidade (PostgreSQL IIC, Grafana) — opcional (`--no-observability`)
5. Build da imagem (opcional, `--build`)
6. Start Copilot API
7. RAG ingest (opcional, `--skip-ingest`, `--reset-rag`)

**Flags:**
| Flag | Comportamento |
|---|---|
| `--build` | `docker compose build api` antes do `up` |
| `--skip-ingest` | Pula reindexação do RAG |
| `--reset-rag` | Recria collection (DA-06 fix) |
| `--no-observability` | Não sobe PostgreSQL + Grafana |

**Health wait:**
```bash
wait_http() {
  local url=$1 label=$2 retries=${3:-30}
  for i in $(seq 1 "$retries"); do
    if curl -sf "$url" >/dev/null 2>&1; then ok "$label disponível"; return 0; fi
    sleep 3
  done
  warn "$label não respondeu em $((retries * 3))s — verifique: docker compose logs"
}
```

---

## Fixtures

Nenhum fixture específico (imagens são geradas via `docker compose build`).

---

## Padrões

### Variáveis de Ambiente

- `OLLAMA_HOST` → `http://host.docker.internal:11434` (acesso ao host)
- `QDRANT_URL` → `http://qdrant:6333` (service name no compose network)
- `DATABASE_URL` → `postgresql+asyncpg://...@postgres:5432/...` (service name)
- `CONTAINER_DATABASE_URL` → sobrescreve `DATABASE_URL` no modo container
- `DATABASE_URL` (host) → aponta para `127.0.0.1` (no `.env` do host)

### Healthchecks

| Serviço | Endpoint/Comando | Intervalo |
|---|---|---|
| `api` | `GET /health` (via `urllib`, não curl/wget) | 30s |
| `qdrant` | `bash -c ':> /dev/tcp/127.0.0.1/6333'` | 10s |
| `postgres` | `pg_isready -U iic -d iic` | 10s |
| `ollama` | `ollama list` (CLI) | 15s |
| `neo4j` | `wget -qO- http://localhost:7474` | 15s |
| `redis` | `redis-cli ping` | 10s |

### Restart Policies

| Serviço | Policy | Objetivo |
|---|---|---|
| `api`, `worker`, `qdrant`, `ollama`, `neo4j`, `redis` | `unless-stopped` | Reiniciar após falha |
| `grafana`, `postgres` | `unless-stopped` | Sempre ativos |
| `reporter` | `no` | Executa e sai (cron) |

---

## Gates

| Gate | Origem | Verificação |
|---|---|---|
| `da_24_applied` | `app/evaluation/gates.py` | `Dockerfile` usa `--frozen`, non-root user, CMD direto no venv |
| `healthcheck_present` | `app/evaluation/gates.py` | `app/main.py::health()` responde, `Dockerfile` tem `HEALTHCHECK` |
| `compose_profiles` | `app/evaluation/gates.py` | `docker-compose.yml` tem perfis `observability`, `graphrag`, `async` |

---

## Exercícios

1. **Startar stack completo:**

```bash
./scripts/start-docker.sh
# Verify: http://localhost:8000 (Copilot), http://localhost:6333 (Qdrant)
```

2. **Startar com observabilidade:**

```bash
./scripts/start-docker.sh --no-observability
# Verify: Grafana não sobe (porta 3001 não aberta)
```

3. **Rebuild manual da imagem:**

```bash
docker compose build api
docker compose up -d api
```

4. **Ver logs em tempo real:**

```bash
docker compose logs -f api
```

5. **Reset RAG (insecure, só dev):**

```bash
./scripts/start-docker.sh --reset-rag
# Verify: collection `sap_reference_library` recriada no Qdrant
```

---

## Invariantes

1. **Build reproduzível** — `uv.lock` + `--frozen`, sem `uv sync` durante build
2. **Não-root user** — `appuser`, uid 1000, `runAsNonRoot: true` no PodSecurity
3. **Healthcheck via stdlib** — `urllib` (não `curl`/`wget`) → não requer `apt install`
4. **Service names no compose network** — `postgres`, `qdrant`, `neo4j`, `redis`
5. **Ollama no host** — `host.docker.internal:11434` (não container)

---

## Limitações

1. **Sem secrets management** — senhas via `POSTGRES_PASSWORD`, `NEO4J_PASSWORD` em `.env` (não Vault/KMS)
2. **Sem ingress controller** — portMapping direto (8000, 6333, 3001, 7474), não Ingress/Route
3. **Sem autoscaling** — replicas fixas (não HPA/KEDA)
4. **Sem configmap/secret provisionado** — variáveis via `env_file: .env`

---

## Referências

- DA-24: Build reproduzível (Dockerfile multi-stage, non-root user, CMD direto)
- DA-34: Healthcheck nos serviços do compose (qdrant, ollama, neo4j, redis)
- DA-41: Circuit breaker com Redis (perfil `async`)
- DA-26: AI Gateway (policy + circuit breaker + budget)
- `Dockerfile`: build definition
- `docker-compose.yml`: services, profiles, `x-common-env`
- `scripts/start-docker.sh`: orchestration script
- `docs/DEPLOYMENT.md`: deployment guia (DA-24)
- `docs/DEPLOY.md`: passo a passo (DA-24)

---

## DAs relevantes

| DA | O que é | Onde |
|---|---|---|
| DA-24 | Build reproduzível (Dockerfile multi-stage, non-root user, CMD direto) | `Dockerfile`, `scripts/start-docker.sh` |
| DA-34 | Healthcheck nos serviços do compose (qdrant, ollama, neo4j, redis) | `docker-compose.yml` |
| DA-41 | Circuit breaker com backend Redis | `app/circuit_breaker.py`, `app/a2a/task_store.py` |
| DA-26 | AI Gateway (policy + circuit breaker + budget) | `app/llm/gateway.py` |
