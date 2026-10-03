# DA_AULA_XX_DEPLOY: Deploy do Integration Incident Copilot

## Onde Este Documento Vive

- **Diretório:** `docs/DA_AULA_XX_DEPLOY.md`
- **Trilha:** Trilha de estudos do Integration Incident Copilot
- **Data de criação:** 2026-10-02
- **Modelo canônico:** `qwen3-coder-next:latest`

---

## 1. Objetivo

Documentar os cenários de deploy da solução Integration Incident Copilot, desde avaliação local até produção em cluster Kubernetes.

**Públicos-alvo:**
- **Evaluadores:** deploy local em ~3 min (sem Ollama) ou ~2 horas (com modelo canônico)
- **Developers:** desenvolvimento local com infra em container ( modo nativo vs container)
- **Ops:** implantação em produção com autenticação obrigatória, soberania de dados e monitoring

**Escopo:**
- Avaliação local (máquina do interessado)
- Desenvolvimento local (app nativo + infra em container)
- Produção (Docker Compose com chaves fixas)
- Kubernetes (Kyma, genérico, on-premise)
- Cloud gerenciado (Cloud Run, ECS, Container Apps)

---

## 2. Arquitetura de Deploy

### 2.1 Cenários Suportados

| Cenário | Status | Tempo | Disco |
|---|---|---|---|
| A. Avaliação local | Validado | ~3 min + baixa de modelo | ~1 GB + 51 GB (canônico) |
| B. Dev local (nativo + infra container) | Validado | <1 min | — |
| C. Todo em container | Validado | ~2 min build | 6.73 GB (imagem) |
| D. Produção (autenticação obrigatória) | Validado | Idem C | — |
| E. SAP BTP Kyma Runtime | Não validado | — | — |
| F. Cloud Foundry | Não implementado | — | — |
| G. Kubernetes genérico | Não validado | — | — |
| H. Cloud gerenciado + LLM remoto | Não implementado | — | — |
| I. On-premise / datacenter | Guía com sizing | — | 51 GB (LLM) |

### 2.2 Três Decisões Críticas

Antes de qualquer deploy, defina:

#### Decisão 1 — Mode: `.env` é por modo, não por ambiente

| Onde o app roda | `DATABASE_URL` usa | `QDRANT_URL` usa |
|---|---|---|
| **Nativo** (venv na máquina) | loopback — `127.0.0.1:5432` | `127.0.0.1:6333` |
| **Container** (compose) | nome de serviço — `postgres:5432` | `qdrant:6333` |

**Motivo:** `postgres`, `qdrant` e `neo4j` **só resolvem dentro da rede do compose**. São nomes de serviço, não hosts.

#### Decisão 2 — Ollama: nativo ou containerizado

- **Ollama nativo** (`/usr/local/bin/ollama serve`) → padrão, mais simples
- **Container** (`docker compose --profile container-ollama`) → opt-in, aponta para `host.docker.internal`

#### Decisão 3 — Chave: fixa ou efêmera

- `REQUIRE_AUTH=false` (default) → gera chave aleatória **por processo**
- `REQUIRE_AUTH=true` → recusa subir se chaves vazias (fixar manualmente)

> ⚠️ `REQUIRE_AUTH` **não cobre** `ADMIN_API_KEY`. Configure-a manualmente no `.env`.

### 2.3 Perfil de Infra

| Profile | Serviços | Portas (default) |
|---|---|---|
| `observability` | `qdrant`, `postgres`, `grafana`, `redis` | 6333, 5432, 3001, 6379 |
| `graphrag` | `neo4j` | 7474, 7687 |
| `container-ollama` | `ollama` | 11434 |
| `async` | `redis`, `worker` | 6379 |

---

## 3. Fixtures

### 3.1 `.env.example` — Variáveis de Deploy

| Variável | Obrigatória (production) | Descrição |
|---|---|---|
| `DATABASE_URL` | ✅ | PostgreSQL async (nativo: loopback; container: `postgres:5432`) |
| `QDRANT_URL` | ✅ | Qdrant (nativo: `127.0.0.1:6333`; container: `qdrant:6333`) |
| `API_KEY` | ✅ | Autenticação `/diagnose`, `/a2a`, webhook (`REQUIRE_AUTH=true`) |
| `REQUIRE_AUTH` | ❌ (só production) | `true` = sem geração automática de chaves |
| `NEO4J_URI` | ❌ (GraphRAG opt-in) | Bolt protocol (`neo4j:7687` no compose) |
| `NEO4J_PASSWORD` | ❌ (GraphRAG opt-in) | Senha só vale na primeira inicialização do volume |

### 3.2 Docker Compose

**Profiles de exemplo:**
```bash
# Avaliação + observabilidade
docker compose --profile observability up -d qdrant postgres grafana redis

# Só infra (sem API)
docker compose up -d qdrant postgres grafana redis

# Com Neo4j (GraphRAG)
docker compose --profile graphrag up -d neo4j
```

**Verificação:**
```bash
docker compose ps
# Esperado: api (exited/running), qdrant, postgres, grafana
```

### 3.3 Scripts de Orquestração

| Script | Objetivo |
|---|---|
| `scripts/start-local.sh` | Start app nativo com infra container |
| `scripts/start-docker.sh` | Start container completo (all profiles) |
| `scripts/stop-docker.sh` | Shutdown e prune de volumes |

---

## 4. Padrões

### 4.1 Cenário A: Avaliação Local (3 min + modelo)

**Objetivo:** Alguém nunca viu o projeto → diagnóstico real no navegador.

**Pré-requisitos:** Python 3.12+, `uv`, Ollama, **Qdrant** (única infra obrigatória)

**Passos:**
```bash
# 1. Clonar e sync
git clone <url> && cd integration-incident-copilot
uv sync

# 2. Qdrant (única infra obrigatória)
docker run -d --name qdrant -p 6333:6333 qdrant/qdrant
curl -s localhost:6333/healthz  # {"status":"ok"}

# 3. Ollama + modelo (51 GB → ~1h dependendo da banda)
ollama serve &
ollama pull qwen3-coder-next:latest

# 4. Configurar `.env` (exemplo mínimo)
cp .env.example .env
# Ajustar `QDRANT_URL=http://127.0.0.1:6333` (nativo)

# 5. Base de conhecimento (pequena)
uv run python -m app.rag.ingest --target incidents

# 6. Subir API (fixar chave!)
export API_KEY=chave-local-avaliacao
uv run uvicorn app.main:app --reload --port 8000

# 7. Autenticar e testar
curl -s http://localhost:8000/diagnose \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"incident_description":"IDoc travado em 03","connector_type":"odata"}' | jq
```

**Chaves efêmeras por default:**
- Sem `API_KEY` fixada no `.env`, o startup gera uma chave aleatória **por processo**
- No log: `WARNING: API_KEY is not set, generating ephemeral key`
- Para testes contínuos: fixar `API_KEY` no `.env` ou exportar antes de subir

**O que falta para uma avaliação completa:**
- [ ] `reference_library` (21 GB / ~1 dia de ingestão) → fallback DA-17 inerte
- [ ] `llm_model` canônico (51 GB) → usar `qwen3-coder:latest` (18 GB) para smoke test

### 4.2 Cenário B: Desenvolvimento Local (nativo + infra container)

**Objetivo:** Debug no IDE com infra real e isolada.

**`docker compose` com profiles:**
```bash
docker compose --profile observability up -d qdrant postgres grafana redis
docker compose --profile graphrag      up -d neo4j     # opt-in
```

**`.env` no modo nativo (loopback):**
```dotenv
DATABASE_URL=postgresql+asyncpg://iic:iic@127.0.0.1:5432/iic
QDRANT_URL=http://127.0.0.1:6333
NEO4J_URI=bolt://127.0.0.1:7687
```

**VS Code: 13 launch configs prontas** (`.vscode/launch.json`):
- `Debug: FastAPI (uvicorn)`
- `Debug: graph.py (caso IDoc travado)` (RFC + regra 51)
- `Debug: pytest (só unitários, rápido)`

### 4.3 Cenário C: Todo em Container (Docker Compose)

**Build (não requer `npm run build` manual):**
```bash
docker compose build
# Contexto: 1.87 MB (`.dockerignore` exclui `data/reference_library/`)
# Imagem: 6.73 GB
```

**`.env` no modo container (nomes de serviço):**
```dotenv
DATABASE_URL=postgresql+asyncpg://iic:iic@postgres:5432/iic
QDRANT_URL=http://qdrant:6333
NEO4J_URI=bolt://neo4j:7687
```

**Subir:**
```bash
docker compose --profile observability up -d
curl -s http://localhost:8000/health | jq .status  # "ok"
curl -s http://localhost:8000/ready -o /dev/null -w '%{http_code}'  # 200 ou 503
```

**Reference library (o que falta no container):**
- A imagem só copia `data/sample_docs/` (15 docs, 69 chunks)
- `reference_library` (21 GB) precisa ser ingerida em runtime:
```bash
# 1. Override que monta `./data` do host
cat > docker-compose.ingest.yml <<'YAML'
services:
  api:
    volumes:
      - ./data:/app/data
YAML

# 2. Rodar ingestão como container descartável
docker compose -f docker-compose.yml -f docker-compose.ingest.yml run --rm \
  api python -m app.rag.ingest --target reference
```

### 4.4 Cenário D: Produção (autenticação obrigatória)

Mesma base do Cenário C com chaves fixas.

**`.env` mínimo (production):**
```dotenv
REQUIRE_AUTH=true
API_KEY=<token forte (32 bytes, URL-safe)>
A2A_API_KEY=<token forte>
EVENT_MESH_API_KEY=<token forte>
ADMIN_API_KEY=<token forte>  # NÃO coberto pelo REQUIRE_AUTH
```

**Gerar tokens:**
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Registro de modelos (fail-closed):**
```dotenv
LLM_REGISTRY_DB=true
LLM_CREDENTIALS_MASTER_KEY=<Fernet key>
METERING_ENABLED=true
```

**Soberania de dados (fail-closed):**
```dotenv
DATA_SOVEREIGNTY_MODE=strict  # ou `cloud_with_dlp`
CONFIDENTIAL_ALLOWED_ORIGINS=<lista>
```

**Verificar política atual:**
```bash
curl -s http://localhost:8000/llm/policy -H "X-API-Key: $API_KEY" | jq
```

### 4.5 Cenário E: SAP BTP Kyma Runtime (não validado)

**Bundle em `deploy/kyma/`:** 10 arquivos (deployment, service, apirule, hpa, configmap, namespace, worker, kustomization, secret.example, README)

**Pré-requisitos que o bundle não resolve:**
- **Qdrant** — não implantado; usar Helm chart ou serviço gerenciado
- **Neo4j** — opt-in, fora do bundle
- **Indexação** — rodar ingest **uma vez** após o cluster estar de pé

**Aplicar:**
```bash
# 1. Build + push da imagem
# 2. deployment.yaml: troque <REGISTRY>/<TAG>
# 3. apirule.yaml: troque <CLUSTER_DOMAIN>
# 4. secret.yaml: NUNCA commitar preenchido
kubectl apply -f deploy/kyma/namespace.yaml
kubectl create secret generic iic-secrets --from-env-file=secret.yaml
kubectl apply -k deploy/kyma/
```

**⚠️ Schema do `APIRule` mudou em versões do Kyma:**
```bash
kubectl explain apirule.spec
# Confere `apiVersion: gateway.kyma-project.io/v1beta1` before apply
```

### 4.6 Cenário I: On-Premise / Datacenter

**Decisão 1: Dimensionamento do LLM local**

| Modelo | Disco | Quando usar |
|---|---|---|
| `qwen3-coder-next:latest` | 51 GB | Canônico (DA-12), paridade 10/10 promptfoo |
| `qwen3-coder:latest` | 18 GB | Alternativa menor (perde paridade) |
| `tev1:latest` | 4.5 GB | Sem GPU, só smoke test |
| `nomic-embed-text:latest` | 274 MB | Embedding denso (obrigatório) |

**Decisão 2: Base de conhecimento é grande e local**

- **Reference library:** 21 GB / 2.112 arquivos (~1 dia de ingestão)
- **Qdrant:** ~5 GB para 500k chunks (768 dims), reserve para growth
- **OCR obrigatório** se PDFs digitalizados → estado pula chunks sem texto

**Primeira instalação (sequência sugerida):**
1. Levantar rede e sizing (RLM, rede, TLS)
2. Subir **Qdrant** primeiro (dependência do RAG), validar `/ready`
3. Subir API em modo de estudo (`REQUIRE_AUTH=false`), validar `/health`
4. Indexar `incidents` (minutos), validar `/diagnose` ponta a ponta
5. Indexar `reference` (I.2) em janela própria
6. Só então fixar chaves e `REQUIRE_AUTH=true`
7. Registrar sistema integrado no `/admin` (DA-49/50)

---

## 5. Gates

| Gate | Origem | Verificação |
|---|---|---|
| `da_24_applied` | `app/evaluation/gates.py` | `Dockerfile` usa `--frozen`, non-root user, CMD direto no venv |
| `healthcheck_present` | `app/evaluation/gates.py` | `app/main.py::health()` responde, `Dockerfile` tem `HEALTHCHECK` |
| `compose_profiles` | `app/evaluation/gates.py` | `docker-compose.yml` tem perfis `observability`, `graphrag`, `async` |
| `api_keys_configured` | `app/main.py::_ensure_api_keys_configured()` | `REQUIRE_AUTH=true` + chaves não vazias |
| `data_sovereignty_mode_valid` | `app/llm/gateway.py` | `DATA_SOVEREIGNTY_MODE` é `Literal['strict', 'cloud_with_dlp']` |

---

## 6. Exercícios

1. **Startar stack completo (observability + graphrag):**
   ```bash
   docker compose --profile observability --profile graphrag up -d
   curl -s http://localhost:8000/health | jq .status
   curl -s http://localhost:6333/healthz | jq
   curl -s http://localhost:7474/db/data/ | jq .version
   ```

2. **Validar health vs readiness:**
   ```bash
   # `/health` → sempre 200 se o processo existe (liveness)
   curl -s http://localhost:8000/health | jq .status
   # `/ready` → 200 ou 503 (readiness: dependências OK?)
   curl -s -w '\nHTTP status: %{http_code}\n' http://localhost:8000/ready
   ```

3. **Fazer diagnose sem fixture (mock):**
   ```bash
   export API_KEY=$(uv run python -c "from app.main import settings; print(settings.api_key)")
   curl -s http://localhost:8000/diagnose \
     -H "Content-Type: application/json" \
     -H "X-API-Key: $API_KEY" \
     -d '{"incident_description":"Falha genérica","connector_type":"odata"}' \
     | jq '{agent_domain, is_grounded, evidence_strength}'
   ```

4. **Validar política de soberania (dev ou prod):**
   ```bash
   curl -s http://localhost:8000/llm/policy -H "X-API-Key: $API_KEY" | jq
   # Esperado: `data_sovereignty_mode`, `allowed_origins`, `origins`
   ```

5. **Reset de RAG (só dev, não prod):**
   ```bash
   ./scripts/start-docker.sh --reset-rag
   # Verify: collection recriada no Qdrant (reindexação iniciada em segundo plano)
   ```

---

## 7. Invariantes

1. **`.env` por modo, não por ambiente** — Native: loopback (`127.0.0.1:5432`), Container: nomes de serviço (`postgres:5432`)
2. **Ollama no host por default** — `host.docker.internal:11434`, não container (profile `container-ollama` é opt-in)
3. **Chaves fixas só com `REQUIRE_AUTH=true`** — `REQUIRE_AUTH=false` gera chave efêmera **por processo**, muda em cada restart
4. **`ADMIN_API_KEY` nunca é coberto pelo `REQUIRE_AUTH`** — Configure manualmente no `.env`
5. **Neo4j senha só vale na primeira inicialização** — Trocar `NEO4J_PASSWORD` no `.env` depois não troca no volume → remover `integration-incident-copilot_neo4j_data`
6. **Qdrant host port compartilhado** — Se outro stack usa `6333`, defina `QDRANT_HOST_PORT=6335` e use `QDRANT_URL=http://127.0.0.1:6335`
7. **`reference_library` não está na imagem** — Apenas `data/sample_docs/` (15 docs). Correr ingestão com override volume (DA-17 fallback inerte sem ingestão)

---

## 8. Limitações

1. **Sem secrets management** — Senhas via `POSTGRES_PASSWORD`, `NEO4J_PASSWORD` em `.env` (não Vault/KMS)
2. **Sem ingress controller** — PortMapping direto (8000, 6333, 3001, 7474), não Ingress/Route
3. **Sem autoscaling** — Replicas fixas (não HPA/KEDA)
4. **Sem configmap/secret provisionado** — Variáveis via `env_file: .env`
5. **TLS não terminado no compose** — Terminate TLS na frente (nginx, HAProxy, cloud LB)
6. **Qdrant sem HA** — Single node, sem replicas/shards
7. **Neo4j single instance** — Sem cluster, sem failover automático
8. **Liveness não checa dependências** — `/health` sempre 200 se o processo existe; usar `/ready` para readiness
9. **Chaves efêmeras não servem para clientes MCP/A2A** — CLI tools precisam de chave fixa no `.env`

---

## 9. Referências

- DA-24: Build reproduzível (Dockerfile multi-stage, non-root user, CMD direto) (`deploy/kyma/deployment.yaml`, `Dockerfile`)
- DA-34: Healthcheck nos serviços do compose (qdrant, ollama, neo4j, redis) (`docker-compose.yml`)
- DA-41: Circuit breaker com Redis (perfil `async`) (`app/circuit_breaker.py`)
- DA-26: AI Gateway (policy + circuit breaker + budget) (`app/llm/gateway.py`)
- DA-55: Login web UI (HMAC cookie + out-of-band verification) (`app/auth.py`, `webusers.py`)
- `Dockerfile`: Build definition (multi-stage, non-root, healthcheck)
- `docker-compose.yml`: Services, profiles, `x-common-env`
- `scripts/start-docker.sh`: Orchestration script
- `docs/DEPLOYMENT.md`: Guia completo de deploy (9 cenários, status honesto)
- `docs/DEPLOY.md`: Passo a passo Docker Compose

---

## 10. DAs relevantes

| DA | O que é | Onde |
|---|---|---|
| DA-24 | Build reproduzível (Dockerfile multi-stage, non-root user, CMD direto) | `Dockerfile`, `deploy/kyma/deployment.yaml` |
| DA-34 | Healthcheck nos serviços do compose (qdrant, ollama, neo4j, redis) | `docker-compose.yml` |
| DA-41 | Circuit breaker com backend Redis | `app/circuit_breaker.py`, `app/a2a/task_store.py` |
| DA-26 | AI Gateway (policy + circuit breaker + budget) | `app/llm/gateway.py` |
| DA-39 | Política de soberania de dados (fail-closed, por origem real) | `app/llm/gateway.py`, `app/config.py::DATA_SOVEREIGNTY_MODE` |
| DA-55 | Manutenção de usuários web por `/admin` | `app/webusers.py`, `app/auth.py`, migration 007 |
