# Aula 2 — FastAPI e HTTP (Módulo 2)

## Objetivo

Aprender os fundamentos do framework FastAPI usando o código real do projeto como fonte de verdade:
- Endpoints, validação de entrada e respostas estruturadas (Pydantic)
- Autenticação multi-camada (3 chaves distintas)
- Rate limiting por identidade do cliente
- Lifespan e exception handler global
- OpenAPI integrado com Pydantic

---

## Preparação

```bash
# Ativar ambiente
PATH="$PWD/.venv/bin:$PATH"

# Ver dependências
uv pip list | grep -E 'fastapi|pydantic|slowapi'
```

---

## Endpoints principais do Copilot

O ponto de entrada é `app/main.py`, com o FastAPI app montado em `app=FastAPI(...)`. Abaixo, os endpoints essenciais (todos com `@app.<VERB>`):

| Endpoint | Verbo | Responsabilidade | DA |
|---|---|---|---|
| `/diagnose` | POST | Diagnóstico sincrono de incidente | DA-18 |
| `/diagnose/async` | POST | Diagnóstico assíncrono (fila) | DA-26 |
| `/diagnose/async/{job_id}` | GET |status de job assíncrono | DA-26 |
| `/events/incident` | POST | Webhook CloudEvents (Event Mesh) | DA-23 |
| `/incidents/{id}/verify` | POST | Feedback humano/sistema de diagnóstico | DA-28/DA-50 |
| `/health` | GET | Liveness probe (sem IO externo) | DA-35 |
| `/ready` | GET | Readiness probe (IO real) | DA-35 |
| `/llm/policy` | GET | Policy de soberania de dados | DA-43 |
| `/.well-known/agent-card.json` | GET | Metadata do agente | MCP |

### Endpoint `/diagnose` (POST)

**Contrato de entrada**: `IncidentRequest` (Pydantic, `app/models.py:14-63`)
**Contrato de saída**: `DiagnosisResponse` (Pydantic, `app/models.py:163-300`)

```python
# app/main.py:443-456
@app.post(
    "/diagnose",
    response_model=DiagnosisResponse,
    dependencies=[Depends(verify_session_or_api_key)],
)
@limiter.limit("10/minute")
def diagnose(request: Request, body: IncidentRequest) -> DiagnosisResponse:
    """Diagnostica um incidente de integracao SAP.

    Rate limit: 10 requisicoes por minuto por IP.
    Autenticacao: X-API-Key header (se API_KEY configurado no .env).
    """
    return run_diagnosis(body)
```

**Pontos-chave**:
- `response_model=DiagnosisResponse` → FastAPI usa Pydantic para **validar e serializar** a resposta.
- `dependencies=[Depends(verify_session_or_api_key)]` → 3 chaves compatíveis para autenticação.
- `@limiter.limit("10/minute")` → Rate limiter por identidade do cliente (`request_client_identity()` em `app/rate_limit.py:37-62`).

**`IncidentRequest`** (resumo):
- `description` (str, max 5.000 chars): descrição textual livre do incidente.
- `logs`/`payload` (str | None, max 50.000 chars): logs/payload opcionais.
- `interface_type` (Literal[..., None]): tipo de conector (odata, rfc, servicenow, salesforce, workday, ariba, successfactors, po, cap, apim).
- `identifier` (str | None): identificador específico (ex: nome do iFlow, RFC destination).
- `connector_source_system`, `sensitivity_level`, `pii_detected`, `redaction_applied`: campos SOC/iPaaS opcionais.

**`DiagnosisResponse`** (resumo):
- `probable_root_cause` (str): causa raiz provável.
- `model_confidence` (float 0.0–1.0): autoavaliação do LLM (ajustada por guardrails).
- `diagnosis_confidence` (float 0.0–1.0): métrica calculada pelo pipeline (`evidence_strength * model_confidence`).
- `next_steps` (list[str]): próximos passos.
- `report_markdown` (str): relatório completo em markdown.
- `evidence` (list[Evidence]): lista de fontes reais consultadas (conector, RAG, GraphRAG, busca web, usuário).
- `llm_provider_used`, `llm_model`, `prompt_version`, `prompt_digest`: proveniência do LLM.
- `incident_id` (str | None): ID no grafo Neo4j (DA-28).
- `trace_id` (str | None): ID no Langfuse (DA-26/DA-50).

---

### Endpoint `/health` (GET)

**Responsabilidade**: liveness probe — confirma apenas que o processo FastAPI está de pé, **sem IO externo**.

```python
# app/main.py:407-420
@app.get("/health")
def health() -> dict:
    """Liveness probe - confirma apenas que o processo FastAPI esta de pe.
    NAO faz chamadas externas (Qdrant/Ollama) e sempre retorna 200.

    Tambem devolve o estado dos conectores/infra derivado do .env, que o
    frontend (StatusView) consulta."""
    return {"status": "ok", **_status_body()}
```

**Por que não faz IO?** Kubernetes usa liveness para decidir se **REINICIA** o pod. Se condicionarmos a liveness a dependências externas (ex: Qdrant fora do ar), causamos restart-loop — o重启 não resolve o problema. Dependências externas ficam em `/ready` (readiness).

**Estado de infra** (`_status_body()`):
```python
{
    "connectors": {"odata": True, "rfc": True, ...},
    "infra": {
        "llm_provider": "ollama",
        "graph_rag_enabled": False,
        "langfuse_enabled": False,
        "async_queue_enabled": False,
        "auth_required": True,
    },
}
```

---

### Endpoint `/ready` (GET)

**Responsabilidade**: readiness probe — IO real em Qdrant/Ollama/Redis.

```python
# app/main.py:422-441
@app.get("/ready")
def ready() -> Response:
    """Readiness probe - probe real em Qdrant/Ollama/Redis (DA-35)."""
    infra_probes = _probe_infra_services()
    required = _required_services()
    all_ok = all(
        v == "ok" or (v == "not_configured" and name not in required)
        for name, v in infra_probes.items()
    )
    status_code = 200 if all_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ok" if all_ok else "degraded", **_status_body(), "services": infra_probes},
    )
```

**Requisitos obrigatórios do modo ativo** (`_required_services()`):
- `qdrant`: sempre (RAG e núcleo do diagnóstico).
- `ollama`: quando `llm_provider == "ollama"`.

**Resultado**: 200 OK se todos os serviços configurados estão OK; 503 Service Unavailable se algum obrigatório falha. Kubernetes usa readiness para **remover o pod do pool de roteamento** até a dependência voltar (sem reiniciá-lo).

---

### Endpoint `/events/incident` (POST)

**Responsabilidade**: DA-23 — Webhook CloudEvents (SAP Event Mesh) → disparo automático de diagnóstico em background.

```python
# app/main.py:520-561
@app.post(
    "/events/incident",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_event_mesh_api_key)],
)
@limiter.limit("10/minute")
def incident_event_webhook(
    request: Request,
    envelope: IncidentEventEnvelope,
    background_tasks: BackgroundTasks,
) -> Response:
    """DA-23: recebe envelope CloudEvents e dispara run_diagnosis() em background."""
    try:
        body = handle_incident_event_async(envelope, background_tasks)
    except Exception as exc:
        # 503 se enqueue falhar (Event Mesh reenvia)
        raise HTTPException(status_code=503, detail="Fila de eventos indisponivel.") from exc
    return JSONResponse(status_code=202, content=body)
```

**Contrato de entrada**: `IncidentEventEnvelope` (DA-23, `app/models.py:107-120`):
- `type`: literal `"com.sap.integration.incident.detected.v1"`.
- `source`, `id`, `time`: metadados do CloudEvents.
- `data`: `IncidentEventData` (mesmos campos que `IncidentRequest`).

**Comportamento**:
- 202 Accepted imediato (`BackgroundTasks` sem Redis; RQ durable com `REDIS_URL`).
- Sem worker RQ, jobs ficam "queued" indefinidamente.
- 503 se fila indisponível (Event Mesh reenvia).

---

### Endpoint `/incidents/{id}/verify` (POST)

**Responsabilidade**: DA-28 (VERIFIED_AS) + DA-50 (feedback no banco/graf/langfuse).

```python
# app/main.py:563-662
@app.post(
    "/incidents/{incident_id}/verify",
    dependencies=[Depends(verify_session_or_api_key)],
)
@limiter.limit("10/minute")
def verify_incident_endpoint(
    request: Request, incident_id: str, body: VerifyIncidentRequest
) -> dict[str, str | bool]:
    """Registra confirmacao explícita da causa raiz (3 efeitos independentes)."""
    # 1. SQL (DA-50, best-effort)
    sql_updated = record_verification(...)
    # 2. Grafo Neo4j (DA-28, GraphRAG_enabled)
    graph_updated = verify_incident(...) or None
    # 3. Langfuse (feedback score)
    langfuse_scored = ... or None
    # 400 se nenhum efeito puder acontecer
    if not graph_updated and not langfuse_scored and not sql_updated:
        raise HTTPException(status_code=400, detail="Nada para registrar...")
    return {
        "incident_id": incident_id,
        "status": "verified",
        "graph_updated": graph_updated,
        "langfuse_scored": langfuse_scored,
        "sql_updated": sql_updated,
    }
```

**Contrato de entrada**: `VerifyIncidentRequest` (`app/models.py:302-351`):
- `root_cause` (str): causa raiz confirmada.
- `verified_by` (Literal["human", "system"], default "human").
- `correct` (bool | None): veredito "o diagnostico original estava correto?".
- `trace_id` (str | None): ID Langfuse para score de feedback.

**3 efeitos independentes**:
1. **Grafo Neo4j** (`verify_incident`): grava `VERIFIED_AS` com `root_cause`, `verified_by`, `verified_at`.
2. **Langfuse** (opcional): cria score `diagnosis_correct` (booleano) no trace referenciado por `trace_id`.
3. **PostgreSQL** (DA-50, `record_verification`): grava `verified_at`, `diagnosis_correct`, `verified_by`, `verified_root_cause` na tabela `incidents`.

**400 se NENHUM dos três puder acontecer**: GraphRAG desligado + sem DATABASE_URL + nenhum trace_id/correct válido.

---

### Endpoint `/llm/policy` (GET)

**Responsabilidade**: DA-43 — policy de soberania de dados (auditoria de origem real/origin).

```python
# app/main.py:665-679
@app.get("/llm/policy", dependencies=[Depends(verify_session_or_api_key)])
def llm_policy() -> dict:
    """DA-43: policy de soberania de dados do AI Gateway, em vigor agora.

    Mostra, por provider, a ORIGIN real resolvida, se pode receber dado
    'public' e 'confidential', e o motivo. Nao inclui nenhuma credencial.
    """
    return describe_effective_policy()
```

**Objetivo**: quadrar questionários de segurança de cliente (demonstrar onde os dados vão e por quê).

---

### Endpoint `/.well-known/agent-card.json` (GET)

**Responsabilidade**: metadata do agente (MCP/Agent2Agent). Formato: `get_agent_card()` (`app/main.py:681-683`).

---

## Autenticação multi-camada (DA-18/DA-54)

### Três chaves distintas para três superfícies

| Superfície | Header | Daonde vem | Porta |
|---|---|---|---|
| API convencional (curl, MCP, CLI) | `X-API-Key` | `.env:API_KEY` | `/diagnose`, `/events/incident`, `/llm/policy`, `/incidents/{id}/verify` |
| Agent2Agent (A2A) | `X-A2A-Api-Key` | `.env:A2A_API_KEY` | `/a2a/*` (JSON-RPC 2.0) |
| Event Mesh | `X-Event-Mesh-Api-Key` | `.env:EVENT_MESH_API_KEY` | `/events/incident` |

### `/diagnose` aceita **duas** vias: chave ou sessão (DA-54)

```python
# app/main.py:443-456
@app.post("/diagnose", dependencies=[Depends(verify_session_or_api_key)])
def diagnose(...) -> DiagnosisResponse:
    ...
```

**`verify_session_or_api_key`** (`app/main.py`):
- Se `X-API-Key` presente → valida com `API_KEY`.
- Se cookie de sessão presente → valida com `SESSION_SECRET` (HMAC-SHA256).
- Se nenhuma das duas → 401.

### Login de sessão (DA-54): `POST /auth/login`

```python
# app/auth.py:242-284
@router.post("/login")
@limiter.limit("5/minute")
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    db: DbDep,
) -> dict[str, str | bool | int]:
    """Troca usuario+senha por cookie de sessao HttpOnly."""
    users = _users()  # .env (WEB_UI_USERS)
    senha_ok = bool(users) and verify_password(users, body.username, body.password)
    if not senha_ok:
        senha_ok = await verify_login_db(db, body.username, body.password)
    if not senha_ok:
        raise HTTPException(status_code=401, detail="usuario ou senha invalidos")
    token = sign_session(body.username, ttl, settings.session_secret)
    response.set_cookie(value=token, **session_cookie_kwargs())
    return {"authenticated": True, "username": body.username, "ttl_hours": ttl_hours}
```

**Duas fontes**: `.env` (bootstrap) + banco (`web_users`, DA-55).
**Cookie**: `HttpOnly`, `SameSite=Strict`, assinado HMAC-SHA256 (`sign_session`).
**Rate limit**: 5/min (mais apertado que `/diagnose` por ser autenticação).

### Ativação em duas etapas (DA-55): `POST /auth/verify/email` e `/auth/verify/phone`

- **Etapa 1**: token por e-mail → ativa login via banco.
- **Etapa 2**: código de 6 dígitos por telefone → conta ativa (`status=active`).

---

## Rate limiting por identidade (DA-26/P1.3)

**Problema**: rate limiter original usava só IP → penaliza clientes atrás de NAT/proxy.
**Solução**: identificador do cliente pela ordem:

```python
# app/rate_limit.py:37-62
def request_client_identity(request: Request) -> str:
    """Identifica o cliente pelo token de autenticacao (quando presente) ou IP."""
    a2a_key = request.headers.get("X-A2A-Api-Key")
    if a2a_key:
        return f"a2a:{a2a_key}"  # prioridade 1

    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"apikey:{api_key}"  # prioridade 2

    if request.client:
        return request.client.host  # fallback: IP
    return "unknown"
```

**Comportamento**:
- Dois clientes A2A com chaves diferentes NÃO compartilham bucket (mesmo IP).
- Fallback para IP mantém comportamento para chamadas sem autenticação.

**Rate limits em `/diagnose`**:
- `@limiter.limit("10/minute")` por identidade (não por IP).

---

## Lifespan (inicialização e cleanup)

```python
# app/main.py:108-187
@app.on_event("lifespan")
async def lifespan(app: FastAPI):
    """Valida chaves, sessão HMAC, credenciais Neo4j; inicializa constraints GraphRAG."""
    _ensure_api_keys_configured()
    ensure_session_secret_configured()
    if settings.graph_rag_enabled:
        await _init_graph_constraints()
    yield
```

**Validações críticas**:
- `API_KEY`, `A2A_API_KEY`, `EVENT_MESH_API_KEY`: gera se vazias (WARNING), nunca deixa vazio.
- `SESSION_SECRET`: gera se vazia (WARNING), cookie não é assinado sem segredo.
- GraphRAG: cria constraints (`INCIDENT_ID_UNIQUE`, `VERIFIED_AS_COMPOUND`) se habilitado.

---

## Exception handler global

```python
# app/main.py:211-260
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    return await http_exception_handler(request, exc)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "detail": "ValidationError",
            "errors": exc.errors(),
            "body": exc.body,
        },
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    error_id = str(uuid.uuid4())
    logger.error("Erro inesperado (error_id=%s): %s", error_id, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error_id": error_id,
            "detail": "Internal Server Error. Report error_id ao suporte.",
        },
    )
```

**500 → `error_id` estruturado**: UUID único para rastreamento.
**422 → `ValidationError` detalhada**: lista de erros de validação + request body.

---

## OpenAPI integrado com Pydantic

FastAPI **gera OpenAPI automaticamente** a partir das rotas e modelos Pydantic.

**Exemplo**: `/diagnose` → schema `IncidentRequest` e `DiagnosisResponse` no OpenAPI.

**`Field(description=...)` → instruções para LLM** via LangChain (usado em `app/agent/nodes.py`):
```python
class IncidentRequest(BaseModel):
    sensitivity_level: Literal["public", "internal", "confidential", "secret"] | None = Field(
        default=None,
        description="Nivel de sensibilidade declarado pelo cliente. None = classificado pelo pipeline.",
    )
```

**`Literal` → enumeração de valores válidos**: OpenAPI gera `enum`, Pydantic rejeita valor desconhecido com 422.

---

## Recapitulação

| Conceito | Fonte | DA |
|---|---|---|
| Endpoints e validação | `app/main.py` | |
| Autenticação multi-camada | `app/main.py` + `app/auth.py` | DA-18, DA-54 |
| Rate limiter por identidade | `app/rate_limit.py` | DA-26/P1.3 |
| Lifespan | `app/main.py` | DA-35 |
| Exception handler | `app/main.py` | |
| OpenAPI + Pydantic | FastAPI + Pydantic | |

---

## Próximo passo

Módulo 3: **LangGraph e LLM** — o grafo de nós (`nodes.py`) e o ponto de entrada (`graph.py`).
Ver também: `docs/TUTORIAL_ARQUITETURA_DEBUG.md` (debug do grafo no VS Code).

---

## Referências rápidas

- `/diagnose` schema: `app/models.py:14-63` (entrada), `app/models.py:163-300` (saída)
- `verify_session_or_api_key`: `app/main.py` (dependency)
- `request_client_identity`: `app/rate_limit.py:37-62`
- Lifespan: `app/main.py:108-187`
- Exception handlers: `app/main.py:211-260`
- `/auth/login`: `app/auth.py:242-284`
- Auth multi-camada (3 chaves): `app/main.py:54`, `app/rate_limit.py:37-62`
