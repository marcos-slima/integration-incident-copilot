# URLs e Credenciais — Integration Incident Copilot

> Última atualização: 2026-10-23
> Para testes locais, o app gera chaves automaticamente se não definidas no `.env`.

---

## Acesso Principal (Web UI)

| Funcionalidade | URL | Método | Auth | Acesso |
|---|---|---|---|---|
| **Login da UI** | `POST /auth/login` | POST | Form username + password | Web |
| **Logout** | `POST /auth/logout` | POST | Sessão (cookie) | Web |
| **Sessão atual** | `GET /auth/session` | GET | Cookie | Web |
| **Verificar e-mail** | `POST /auth/verify/email` | POST | Token por e-mail | Web |
| **Verificar telefone** | `POST /auth/verify/phone` | POST | Código por SMS | Web |
| **Admin UI (root)** | `GET /admin` | GET | Sessão (cookie) | Web |
| **Admin UI Modelos** | `GET /admin/models` | GET | Sessão | Web |
| **Admin UI Uso** | `GET /admin/usage` | GET | Sessão | Web |
| **Admin UI Sistemas** | `GET /admin/systems` | GET | Sessão | Web |
| **Admin UI Incidentes** | `GET /admin/incidents` | GET | Sessão | Web |
| **Admin UI Usuários** | `GET /admin/users` | GET | Sessão | Web |
| **Admin UI Web search** | `GET /admin/web-search` | GET | Sessão | Web |

> O frontend React roda na porta 8000 (FastAPI serve `/` e `/admin/*`).
> Em modo dev, Vite roda na porta 5173 e faz proxy para FastAPI.

---

## Diagnóstico (API — Automatizado e Webhooks)

| Funcionalidade | URL | Método | Auth | Exemplo curl |
|---|---|---|---|---|
| **Diagnóstico síncrono** | `POST /diagnose` | POST | `X-API-Key` | `curl -H "X-API-Key: KEY" -d '{"description":"..."}' http://127.0.0.1:8000/diagnose` |
| **Diagnóstico assíncrono** | `POST /diagnose/async` | POST | `X-API-Key` | `curl -H "X-API-Key: KEY" -d '{"description":"..."}' http://127.0.0.1:8000/diagnose/async` |
| **Status do job async** | `GET /diagnose/async/{job_id}` | GET | `X-API-Key` | `curl -H "X-API-Key: KEY" http://127.0.0.1:8000/diagnose/async/ID` |
| **Ingestão de eventos (webhook)** | `POST /events/incident` | POST | `X-Event-Mesh-Api-Key` | `curl -H "X-Event-Mesh-Api-Key: KEY" -d '@event.json' http://127.0.0.1:8000/events/incident` |
| **Verificar incidente (HITL)** | `POST /incidents/{id}/verify` | POST | Cookie (sessão UI) | Web |
| **Review de incidente** | `POST /incidents/{id}/review` | POST | Cookie (sessão UI) | Web |

> O endpoint `/diagnose` não fica exposto se `API_KEY` não estiver definida (fail-closed).

---

## Admin API (Máquinas — CLI/Terraform/PagerDuty)

| Funcionalidade | URL | Método | Auth |
|---|---|---|---|
| **Registry status** | `GET /admin/api/registry/status` | GET | `X-API-Admin-Key` |
| **Runtime config (read)** | `GET /admin/api/runtime-config` | GET | `X-API-Admin-Key` |
| **Runtime config (write)** | `PATCH /admin/api/runtime-config` | PATCH | `X-API-Admin-Key` |
| **Effective config** | `GET /admin/api/effective-config` | GET | `X-API-Admin-Key` |
| **Check-activate** | `GET /admin/api/runtime-config/check-activate` | GET | `X-API-Admin-Key` |
| **Listar modelos** | `GET /admin/api/models` | GET | `X-API-Admin-Key` |
| **Criar modelo** | `POST /admin/api/models` | POST | `X-API-Admin-Key` |
| **Obter modelo** | `GET /admin/api/models/{model_id}` | GET | `X-API-Admin-Key` |
| **Atualizar modelo** | `PATCH /admin/api/models/{model_id}` | PATCH | `X-API-Admin-Key` |
| **Remover modelo** | `DELETE /admin/api/models/{model_id}` | DELETE | `X-API-Admin-Key` |
| **Preview de roteamento** | `GET /admin/api/routing/preview` | GET | `X-API-Admin-Key` |
| **Listar credenciais** | `GET /admin/api/credentials` | GET | `X-API-Admin-Key` |
| **Atualizar credencial** | `PUT /admin/api/credentials/{provider_origin:path}` | PUT | `X-API-Admin-Key` |
| **Remover credencial** | `DELETE /admin/api/credentials/{provider_origin:path}` | DELETE | `X-API-Admin-Key` |
| **Listar uso** | `GET /admin/api/usage` | GET | `X-API-Admin-Key` |
| **Resetar uso** | `POST /admin/api/usage/{provider_origin:path}/{model_id}/reset` | POST | `X-API-Admin-Key` |
| **Listar sistemas** | `GET /admin/api/systems` | GET | `X-API-Admin-Key` |
| **Criar sistema** | `POST /admin/api/systems` | POST | `X-API-Admin-Key` |
| **Obter sistema** | `GET /admin/api/systems/{system_id}` | GET | `X-API-Admin-Key` |
| **Atualizar sistema** | `PATCH /admin/api/systems/{system_id}` | PATCH | `X-API-Admin-Key` |
| **Remover sistema** | `DELETE /admin/api/systems/{system_id}` | DELETE | `X-API-Admin-Key` |
| **Listar sistemas/incidents** | `GET /admin/api/systems/{system_id}/incidents` | GET | `X-API-Admin-Key` |
| **Listar incidentes (admin)** | `GET /admin/api/incidents` | GET | `X-API-Admin-Key` |
| **Obter incidente (admin)** | `GET /admin/api/incidents/{incident_id}` | GET | `X-API-Admin-Key` |
| **Listar usuários (admin)** | `GET /admin/api/users` | GET | `X-API-Admin-Key` |
| **Criar usuário (admin)** | `POST /admin/api/users` | POST | `X-API-Admin-Key` |
| **Token de e-mail** | `POST /admin/api/users/{user_id}/email-token` | POST | `X-API-Admin-Key` |
| **Código de telefone** | `POST /admin/api/users/{user_id}/phone-code` | POST | `X-API-Admin-Key` |
| **Atualizar usuário** | `PATCH /admin/api/users/{user_id}` | PATCH | `X-API-Admin-Key` |
| **Remover usuário** | `DELETE /admin/api/users/{user_id}` | DELETE | `X-API-Admin-Key` |
| **Gerar hierarquia ontológica** | `GET /admin/ontology/hierarchy` | GET | `X-API-Admin-Key` |
| **Knowledge: versões** | `POST /admin/api/knowledge/documents/{document_id}/versions` | POST | `X-API-Admin-Key` |
| **Knowledge: história** | `GET /admin/api/knowledge/documents/{document_id}/history` | GET | `X-API-Admin-Key` |
| **Knowledge: rollback** | `POST /admin/api/knowledge/documents/{document_id}/rollback/{version}` | POST | `X-API-Admin-Key` |
| **Reports: export compliance** | `GET /admin/reports/compliance/export` | GET | `X-API-Admin-Key` |
| **Dashboards: compliance** | `GET /admin/dashboard/compliance` | GET | `X-API-Admin-Key` |

> O endpoint `/admin/*` não fica exposto se `ADMIN_API_KEY` não estiver definida (fail-closed).

---

## Serviços e Infraestrutura

| Serviço | URL (dev) | Porta host | Porta container | Perfil |
|---|---|---|---|---|
| **FastAPI (API)** | `http://127.0.0.1:8000` | 8000 | 8000 | default |
| **React (dev)** | `http://localhost:5173` | 5173 | — | n/a |
| **Qdrant (RAG)** | `http://127.0.0.1:6333` | 6333 | 6333 | default |
| **Ollama (LLM)** | `http://127.0.0.1:11434` | 11434 | 11434 | (host) |
| **PostgreSQL (persistência)** | `http://127.0.0.1:5432` | 5432 | 5432 | `observability` |
| **Grafana (painéis)** | `http://127.0.0.1:3002` | 3002 | 3000 | `observability` |
| **Redis (tasks)** | `http://127.0.0.1:6379` | 6379 | 6379 | `async` |
| **Neo4j (GraphRAG)** | `http://127.0.0.1:7474` | 7474 | 7474 | `graphrag` |

> **Importante:** Em modo container (ex: `docker compose`), os hosts são os nomes dos serviços (`qdrant`, `postgres`, `neo4j`) — **não** `127.0.0.1`.

---

## Observabilidade e Debug

| Funcionalidade | URL | Método | Auth |
|---|---|---|---|
| **Health check** | `GET /health` | GET | — |
| **Readiness** | `GET /ready` | GET | — |
| **OpenAPI spec** | `GET /openapi.json` | GET | — |
| **Swagger UI** | `GET /docs` | GET | — |
| **ReDoc UI** | `GET /redoc` | GET | — |
| **Agent card** | `GET /.well-known/agent-card.json` | GET | — |
| **LLM policy (roteamento)** | `GET /llm/policy` | GET | `X-API-Key` |
| **Metrics (Prometheus)** | `GET /metrics` | GET | `Authorization: Bearer $METRICS_TOKEN` |

---

## Credenciais (`.env`)

### Obrigatórias apenas se ativadas

| Variável | Uso | Default | Generador |
|---|---|---|---|
| `API_KEY` | `/diagnose` endpoint | Aleatória por processo | `openssl rand -hex 16` ou `secrets.token_hex(16)` |
| `ADMIN_API_KEY` | `/admin/api/*` | Aleatória por processo | `openssl rand -hex 16` |
| `A2A_API_KEY` | `/a2a` endpoint | Aleatória por processo | `openssl rand -hex 16` |
| `EVENT_MESH_API_KEY` | `/events/incident` | Aleatória por processo | `openssl rand -hex 16` |
| `SESSION_SECRET` | Sessões HTTP | Aleatória por processo | `openssl rand -hex 16` |
| `POSTGRES_PASSWORD` | Database | `REQUIRED_SET_IN_ENV` (bloqueia) | `python3 -c 'import secrets;print(secrets.token_urlsafe(24))'` |
| `GRAFANA_PASSWORD` | Admin Grafana | `REQUIRED_SET_IN_ENV` (bloqueia) | same |
| `GRAFANA_DB_PASSWORD` | Datasource Grafana | same | same |
| `NEO4J_PASSWORD` | Neo4j | `REQUIRED_SET_IN_ENV` (bloqueia) | same |
| `LLM_CREDENTIALS_MASTER_KEY` | Fernet (DA-60) | vazio | `python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` |
| `LLM_REGISTRY_DB` | Registro de modelos | `false` | — |
| `DATABASE_URL` | Persistência | vazio (sem DB) | — |

### Credenciais externas (sistemas SAP/ServiceNow/etc.)

| Conector | Variáveis principais |
|---|---|
| **RFC (SAP)** | `SAP_ASHOST`, `SAP_SYSNR`, `SAP_CLIENT`, `SAP_USER`, `SAP_PASSWORD` |
| **OData/CPI** | `ODATA_SERVICE_URL`, `ODATA_OAUTH_TOKEN_URL`, `ODATA_CLIENT_ID`, `ODATA_CLIENT_SECRET` |
| **ServiceNow** | `SERVICENOW_INSTANCE_URL`, `SERVICENOW_USERNAME`, `SERVICENOW_PASSWORD` |
| **Salesforce** | `SALESFORCE_INSTANCE_URL`, `SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET` |
| **Workday** | `WORKDAY_TENANT`, `WORKDAY_REST_BASE_URL`, `WORKDAY_TOKEN_URL`, `WORKDAY_CLIENT_ID`, `WORKDAY_CLIENT_SECRET` |
| **Ariba** | `ARIBA_OAUTH_TOKEN_URL`, `ARIBA_BASE_URL`, `ARIBA_CLIENT_ID`, `ARIBA_CLIENT_SECRET` |
| **SuccessFactors** | `SFSF_OAUTH_TOKEN_URL`, `SFSF_BASE_URL`, `SFSF_CLIENT_ID`, `SFSF_CLIENT_SECRET` |
| **CAP** | `CAP_SERVICE_URL`, `CAP_XSUAA_TOKEN_URL`, `CAP_CLIENT_ID`, `CAP_CLIENT_SECRET` |
| **API Management** | `APIM_ANALYTICS_URL` |
| **PO/PI** | `PO_BASE_URL`, `PO_USERNAME`, `PO_PASSWORD` |

> Todos os conectores acima operam em modo **demo/mock** por padrão (sem configuração). Ative apenas se tiver acesso a sistemas reais.

---

## Como gerar credenciais seguras

```bash
# API/Admin/A2A keys
for v in API_KEY ADMIN_API_KEY A2A_API_KEY EVENT_MESH_API_KEY; do
  echo "$v=$(openssl rand -hex 16)" >> .env
done

# Database passwords
for v in POSTGRES_PASSWORD GRAFANA_PASSWORD GRAFANA_DB_PASSWORD NEO4J_PASSWORD; do
  echo "$v=$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')" >> .env
done

# Fernet master key (DA-60)
echo "LLM_CREDENTIALS_MASTER_KEY=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" >> .env

# Session secret (Opcional, define para sessões persistentes)
echo "SESSION_SECRET=$(openssl rand -hex 16)" >> .env
```

> Em produção, use gerenciadores de segredos (Vault, AWS Secrets Manager, Azure Key Vault, BTP Secret Manager).

---

## Perfil Docker Compose

| Perfil | Serviços ativos | Variáveis exigidas |
|---|---|---|
| **(nenhum)** | `api`, `qdrant` | nenhuma (demo/mock) |
| `observability` | `postgres`, `grafana`, `reporter` | `POSTGRES_PASSWORD`, `GRAFANA_PASSWORD`, `GRAFANA_DB_PASSWORD` |
| `graphrag` | `neo4j` | `NEO4J_PASSWORD` |
| `async` | `redis`, `worker` (requer `REDIS_URL`) | `REDIS_PASSWORD` (opcional) |
| `container-ollama` | `ollama` (containerizado) | nenhuma |
| `mailpit` | `mailpit` (SMTP mock) | nenhuma |

> O `.env.example` deja as variáveis de perfil **comentadas**. Descomente/defina antes de ativar os perfis.

---

## Endpoints principais para testes rápidos

```bash
# 1. Ingestão da base de conhecimento
uv run python -m app.rag.ingest --target incidents --reset

# 2. Diagnóstico simples (demo/mock)
curl -H "X-API-Key: $(grep '^API_KEY=' .env | cut -d= -f2-)" \
  -H "Content-Type: application/json" \
  -d '{"description": "iFlow falhando com erro 401", "interface_type": "odata", "identifier": "DEMO"}' \
  http://127.0.0.1:8000/diagnose

# 3. Configuração efetiva (com segredos mascarados)
uv run python -m app.config
```

---

## Notas de segurança

1. **Sem `.env`**: o app gera chaves aleatórias por processo (WARNING no log), mas recusa subir se `DATABASE_URL` for definido sem `LLM_CREDENTIALS_MASTER_KEY`.
2. **Sentinelas**: `REQUIRED_SET_IN_ENV` bloqueia Postgres/Grafana/Neo4j se as senhas não forem alteradas.
3. **Fail-closed**: sem `API_KEY`/`ADMIN_API_KEY`/`A2A_API_KEY`, os endpoints correspondentes não expõem rotas.
4. **Fernet (DA-60)**: obriga `LLM_CREDENTIALS_MASTER_KEY` quando `DATABASE_URL` está configurada (criptografa `evidence_json` e `llm_credentials`).

---

**Referências:**
- `.env.example` — lista completa de variáveis com comentários
- `docker-compose.yml` — infraestrutura e perfis
- `docs/GETTING_STARTED.md` — passo a passo de instalação
- `docs/DECISOES_DE_ARQUITETURA.md` — decisões de segurança (DA-46/47/60/67)
