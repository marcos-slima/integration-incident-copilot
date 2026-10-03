# Módulo 2 — Gateway FastAPI e Roteamento

Este documento é a fonte única de verdade para a arquitetura de roteamento, autenticação, rate limiting e segurança da API HTTP do SAP Integration Incident Copilot. Baseia-se na leitura de `app/main.py`, `app/auth.py`, `app/admin/security.py`, `app/rate_limit.py` e `app/config.py`, vinculando diretamente aos módulos anteriores (Pydantic Settings, conectores, camada LLM).

---

## 1. Arquitetura de Autenticação (DA-18 / DA-54)

### 1.1. Princípios Base (DA-18)

- **Fail-closed por definição**: chaves vazias geram uma chave efêmera no startup com WARNING no log. Nunca "modo aberto".
- **Superfícies isoladas**: cada canal tem sua própria chave dedicada:
  - `X-API-Key`: `/diagnose`, `/mcp`, `/events/incident`
  - `X-A2A-Api-Key`: `/a2a` (Agent2Agent)
  - `X-API-Admin-Key`: `/admin/*` (DA-46)
  - `X-Event-Mesh-Api-Key`: `/events/incident` (DA-23)
- **Comparação segura**: todos os `verify_*` usam `secrets.compare_digest` para evitar timing side-channels.

### 1.2. Duas Vias de Autenticação (DA-54)

| Mecanismo | Destinatário | Header/Cookie | Exemplo |
|---|---|---|---|
| **Máquina-máquina** | API convencional (curl, MCP, outro agente) | `X-API-Key` | `curl -H "X-API-Key: abc" ...` |
| **Humano** | UI web (browser) | Cookie `iic_session` (HttpOnly, SameSite=Strict, HMAC-SHA256) | `POST /auth/login → cookie` |

**Não confundir**: cookies de sessão **não** abrem superfícies exclusivamente de máquina (`/a2a`, `/mcp`, `/admin`). Elas são independentes.

### 1.3. Geração de Chaves Efêmeras (startup)

```python
# app/main.py::_ensure_api_keys_configured()
if not settings.api_key:
    settings.api_key = secrets.token_hex(32)
    logger.warning("API_KEY gerada automaticamente (não segura para produção)")
```

**Locais:**
- `api_key`, `a2a_api_key`, `event_mesh_api_key`, `admin_api_key`, `session_secret`

---

## 2. Endpoints e Dependências (app/main.py)

### 2.1. Endpoints Principais

| Rota | Autenticação | Rate Limit | O que faz |
|---|---|---|---|
| `GET /health` | Nenhuma | Nenhum | Health check simples (sem dependências) |
| `GET /ready` | Nenhuma | Nenhum | Readiness probe (valida infra: Qdrant, Ollama, etc.) |
| `POST /diagnose` | `X-API-Key` | `60/minute` | Diagnóstico via LangGraph (DA-18) |
| `GET /a2a/*` | `X-A2A-Api-Key` | `20/minute` | Endpoints A2A JSON-RPC 2.0 (DA-14) |
| `POST /events/incident` | `X-Event-Mesh-Api-Key` | `20/minute` | CloudEvents → `run_diagnosis()` (DA-23) |
| `GET /mcp/*` | `X-API-Key` | rate limit da ferramenta | Servidor MCP (DA-19) |
| `POST /incidents/{id}/verify` | Cookie ou `X-API-Key` | `10/minute` | Verificação explícita de causa raiz (DA-28/DA-50) |
| `GET /llm/policy` | Cookie ou `X-API-Key` | Nenhum | Policy de soberania (DA-43) |
| `GET /.well-known/agent-card.json` | Nenhuma | Nenhum | Agent Card spec A2A 0.3 |
| `POST /auth/login` | Nenhuma (bootstrap) | `5/minute` | Login sessão web (DA-54) |
| `POST /auth/logout` | Cookie ou `X-API-Key` | Nenhum | Logout sessão web |
| `GET /auth/session` | Cookie ou `X-API-Key` | Nenhum | Estado da sessão web |
| `POST /auth/verify/email` | Nenhuma (token) | `5/minute` | Etapa 1 da ativação (token e-mail) (DA-55) |
| `POST /auth/verify/phone` | Nenhuma (code) | `5/minute` | Etapa 2 da ativação (code phone) (DA-55) |
| `POST /admin/api/*` | `X-API-Admin-Key` | rate limit | API admin (DA-46/47/48/49/50) |
| `GET /admin/*` | Sessão cookie HMAC | — | UI admin Jinja2 (DA-46/49/50) |

### 2.2. Dependências de Autenticação

```python
# app/main.py::verify_session_or_api_key()
def verify_session_or_api_key(
    request: Request,
    api_key: str | None = Security(api_key_header, required=False),
    session_cookie: str | None = Depends(verify_session_cookie),
) -> None:
    if api_key and secrets.compare_digest(api_key, settings.api_key or ""):
        return
    if sess cookie:
        return
    raise HTTPException(status_code=401, detail="X-API-Key ou cookie de sessão inválido")
```

**Apenas `/incidents/{id}/verify` e `/llm/policy` usam `verify_session_or_api_key`** (ambos permitem **ambas** as formas). Demais rotas específicas (`/diagnose`, `/mcp`, `/a2a`, `/events/incident`, `/admin/api/*`) usam sua própria chave dedicada.

---

## 3. Autenticação Sessão Web (app/auth.py)

### 3.1. Formato `WEB_UI_USERS`

```
username:pbkdf2_sha256.600000.<salt_hex>.<hash_hex>
```

- Separador `.` (não `$`) para evitar truncamento no Docker Compose
- Hash PBKDF2-SHA256 com 600.000 iterações
- `session_secret` gerado efêmero se vazio (WARNING no log)

### 3.2. Verificação de Sessão

```python
# app/auth.py::verify_session_cookie()
def verify_session_cookie(cookie: str | None = Cookie(None)) -> bool:
    if not cookie:
        return False
    try:
        username = hmac_verify(cookie, settings.session_secret)
        return username in settings.web_ui_users
    except Exception:
        return False
```

**Fallback:** .env → DB (se `WEB_UI_USERS` ou `web_users` ativos), falha única (401).

### 3.3. Rate Limit `/auth/login`

- 5 requisições por minuto por IP
- Resposta genérica (evita enumerateção de usuário)

---

## 4. Rate Limiting por Identidade (app/rate_limit.py)

```python
# app/rate_limit.py::request_client_identity()
def request_client_identity(request: Request) -> str:
    # Prioridade: X-A2A-Api-Key > X-API-Key > IP cliente
    a2a_key = request.headers.get("X-A2A-Api-Key")
    if a2a_key:
        return f"a2a:{a2a_key}"

    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"api:{api_key}"

    return request.client.host if request.client else "unknown"
```

**Resolução de P1.3:** Rate limit não é apenas por IP — chaves API são identidades.

---

## 5. Idempotência (app/events/idempotency.py)

- `Idempotency-Key = envelope.id` (id único do CloudEvent)
- Redis (`SADD`) ou fallback em memória
- Não sobrescreve rate limit (preocupação distinta)

---

## 6. Lifespan e Validações (app/main.py)

### 6.1. Startup

```python
async def lifespan(app: FastAPI):
    await _validate_configs()
    await _ensure_api_keys_configured()
    await _probe_infra_services()
    yield
```

**Validações:**
- `LLM_ROUTES` vs `known_routes()` (DA-45)
- `llm_route` → `origin real` (DA-43/DA-45)
- `graph_rag_enabled => neo4j_password` (DA-21)

### 6.2. Health Checks

- `GET /health` → `200` (sem dependências)
- `GET /ready` → `200`/`503` (valida infra: Qdrant, Ollama, DB)

---

## 7. Admin API (DA-46/47/48/49/50)

### 7.1. Superfícies Admin

| Superfície | Autenticação | O que faz |
|---|---|---|
| `/admin/api/models` | `X-API-Admin-Key` | CRUD de modelos LLM (DA-46) |
| `/admin/api/credentials` | `X-API-Admin-Key` | Credenciais cifradas com Fernet (DA-47) |
| `/admin/api/usage` | `X-API-Admin-Key` | Metering real (DA-48) |
| `/admin/api/systems` | `X-API-Admin-Key` | Catálogo de sistemas integrados (DA-49) |
| `/admin/api/incidents` | `X-API-Admin-Key` | Correlação incidente ↔ sistema (DA-50) |
| `/admin/api/web-search-sources` | `X-API-Admin-Key` | Configuração de fontes de busca web (DA-57) |
| `/admin/models`, `/admin/systems`, `/admin/usage`, `/admin/incidents`, `/admin/web-search` | Sessão cookie | Páginas Jinja2 (exibe dados via API) |

### 7.2. `/incidents/{id}/verify` (DA-28/DA-50)

**3 ações independentes:**
1. Gravação no grafo Neo4j (GraphRAG habilitado)
2. Feedback no Langfuse (`trace_id`)
3. Atualização best-effort da tabela `incidents`

**400 se nenhuma precondição satisfeita**
**404 se GraphRAG ativo mas `incident_id` ausente no grafo**

---

## 8. Resumo de Superfícies Autenticadas

| Superfície | Header/Cookie | Chave .env | Rate Limit | Objetivo |
|---|---|---|---|---|
| Diagnóstico (`/diagnose`) | `X-API-Key` | `API_KEY` | 60/min | Máquina-máquina |
| A2A (`/a2a`) | `X-A2A-Api-Key` | `A2A_API_KEY` | 20/min | Agent2Agent |
| Event Mesh (`/events/incident`) | `X-Event-Mesh-Api-Key` | `EVENT_MESH_API_KEY` | 20/min | Webhook/AMQP |
| Admin API (`/admin/api/*`) | `X-API-Admin-Key` | `ADMIN_API_KEY` | — | Administração |
| Web UI (`/auth/login`, `/auth/*`) | Cookie HMAC | `SESSION_SECRET` | 5/min para login | Humano |

---

## 9. Trivia e Armadilhas

1. **`.env` é por modo**: nativo (`127.0.0.1`, banco Postgres não resolve) vs container (`postgres`, `qdrant`, `neo4j` como nomes de serviço). Erro fácil: o `.env` de um modo quebra o outro.
2. **`graph_rag_enabled` e `NEO4J_PASSWORD`**: só validado no startup se `neo4j_password` vazio. Primeira inicialização do volume Neo4j (`NEO4J_AUTH`) não é mutável depois.
3. **`llm_provider` vs `llm_route`**: primeiro é "qual provider uso?" (ollama/openai/azure), segundo é "onde o dado pode sair?" (rota auditada, DA-45). Conflito explícito → falha no boot.
4. **`session_secret` não é a mesma de `api_key`**: cookies de sessão usam HMAC, API keys usam `compare_digest` direto. Omitir um não desabilita o outro.
5. **`admin_api_key` isolate o blast radius**: vazamento na admin não quebra `/diagnose` (chave diferente, DA-18).
6. **`idempotency` não sobrescreve rate limit**: são preocupações diferentes (idempotência = "não processar duplicado", rate limit = "limitar volume").
7. **`web_search_sources` é fail-closed sem fallback em código**: `WEB_SEARCH_POLICY=approved` não tem dict de emergência. Falha de DB = `None` (sem busca web), não exceção.
8. **`llm_send_seed=None` default**: se omitido, o destino decide. Se um destino não suporta, falha. Override explícito (`True`/`False`) resolve isso.
9. **`/auth/verify/*` são rotas públicas rate-limited (DA-55)**: token e-mail e code phone são validados por IP (não há sessão), e as respostas são genéricas para evitar enumerateção de usuário.

---

**Próximo passo**: Módulo 3 (Agentes LangGraph + Rule Engine) → `app/agent/graph.py`, `app/agent/nodes.py`, `app/agent/rules.py`, `app/agent/escalation.py`.

**Referência rápida**: `README.md` (Decisões de Arquitetura, seção "### N. Título (DA-N)"), `docs/ARCHITECTURE.md`, `docs/GETTING_STARTED.md`, `docs/QUALITY_GATES.md`.

**Comprovante de execução**: `uv run python -m app.config` exibe configuração efetiva (com valores mascarados), e `uv run uvicorn app.main:app --reload` roda o servidor com todos os endpoints mapeados.

**Commit-ready**: este documento é um registro do Módulo 2 (Gateway FastAPI e roteamento), alinhado com as DAs já implementadas (DA-14, DA-18, DA-22, DA-23, DA-26, DA-28, DA-33, DA-43, DA-44, DA-45, DA-46/47/48, DA-49, DA-50, DA-54, DA-55, DA-57), e pronto para uso em `CLAUDE.md` (seção "Arquitetura de Roteamento" — extensão da seção atual `README.md`).
