# DA_AULA_14 — Camada de Segurança e Governança (DA-18/26/27/41/43/47/48/54/57)

## Objetivo

Documentar a camada de segurança e governança do Integration Incident Copilot, cobrindo:

- Autenticação dedicada por transporte (REST/Events: `X-API-Key`, A2A: `X-A2A-Api-Key`, MCP: `X-API-Key` compartilhada)
- Policy de soberania de dados (DA-26/DA-43: `DATA_SOVEREIGNTY_MODE=strict` bloqueia dados confidenciais em cloud)
- Redação/PII (DA-41: regex-based, cobre e-mail/CPF/IDoc/CNPJ/bearer tokens/senhas)
- Circuit breaker compartilhado (DA-41: Redis + fallback em-memória)
- Capability Registry FAIL-CLOSED (DA-27: `mcp/policy.py::CAPABILITY_REGISTRY`)
- Cifra Fernet de credenciais em repouso (DA-47: `LLM_CREDENTIALS_MASTER_KEY` no `.env`)
- Metering de tokens reais (DA-48: `record_usage_observed` no gateway)
- Login de sessão para UI web (DA-54: cookie HMAC, `PBKDF2-SHA256`, `web_users`).

## Arquitetura

### 1. Autenticação Dedicada por Transporte (DA-18/DA-54)

**DA-18** exige `X-API-Key` em `/diagnose`, `/a2a`, `/mcp` e webhooks CloudEvents.
**DA-54** adiciona uma segunda via em `/diagnose`: cookie de sessão HMAC para a UI web.

Superfícies **so de máquina** (MCP, A2A, Event Mesh, `/admin`) **não aceitam cookie de sessão**:
continuam exigindo suas chaves dedicadas.

#### Arquivo: `app/main.py` (l.220-694)

```python
from app.auth import api_key_header  # DA-54

def _ensure_api_keys_configured() -> None:
    """DA-18/DA-19/DA-23: gerar chaves aleatórias no startup se vazias."""
    if not settings.api_key:
        settings.api_key = secrets.token_urlsafe(32)
        logger.warning("[main] API_KEY gerada aleatoriamente no startup (DA-18/DA-19/DA-23).")
    if not settings.a2a_api_key:
        settings.a2a_api_key = secrets.token_urlsafe(32)
        logger.warning("[main] A2A_API_KEY gerada aleatoriamente no startup (DA-14/DA-18).")

@app.post("/events/incident", status_code=202, ...)
def incident_event_webhook(envelope: IncidentEventEnvelope, background_tasks: BackgroundTasks) -> Response:
    # DA-23: require X-Event-Mesh-Api-Key na rota webhooks Cloudevents
    ...  # usa same api_key_header validation
```

#### Arquivo: `app/auth.py` (DA-54: l.1-383)

**Contrato cookie de sessão** (umanção de usuário → cookie):

```python
# 1. Hash PBKDF2-SHA256 (stdlib, 600k iterações):
PBKDF2_ITERATIONS = 600_000  # OWASP 2023

# 2. Format WEB_UI_USERS:
#    marcos:pbkdf2_sha256.600000.<salt_hex>.<hash_hex>
SECRET_SCHEME = "pbkdf2_sha256"

# 3. Comparação segura (secrets.compare_digest):
from app.auth import hash_password, compare_digest

# 4. Cookie HttpOnly + SameSite=Strict:
response.set_cookie(
    key="iic_session",
    value=signed_token,
    httponly=True,
    samesite="strict",
    secure=settings.debug_mode is False,
)
```

**Comparações seguras** (DA-54: `app/auth.py:81-100`):

```python
SECRET_SCHEME = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 600_000  # OWASP 2023


def hash_password(password: str, scheme: str = SECRET_SCHEME) -> str:
    """Gera hash PBKDF2-SHA256 (padrão Fernet, stdlib)."""
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ITERATIONS)
    return f"{scheme}.{PBKDF2_ITERATIONS}.{salt}.{hashed.hex()}"


def compare_secure(a: str, b: str) -> bool:
    """Comparar senhas ou tokens com secrets.compare_digest."""
    return secrets.compare_digest(a, b)
```

**Limitação conhecida (DA-54):**
`SESSION_SECRET` vazio gera um segredo efêmero no startup com **WARNING no log**.
Autenticação **fail-closed**: sem `WEB_UI_USERS` configurado, `/auth/login` responde 401 SEMPRE.

---

### 2. Policy de Soberania de Dados (DA-26/DA-43)

**DA-26**: AI Gateway (`app/llm/gateway.py`) é o ÚNICO ponto de entrada para o LLM.
**DA-43**: Policy verificada **por origin real**, não por rótulo (`openai` apontando para Gemini ≠ `openai` apontando para api.openai.com).

#### Arquivo: `app/llm/gateway.py` (l.1-508)

**Sensibilidade classificada** (DA-26: l.101-140):

```python
Sensitivity = Literal["confidential", "public"]


def classify_sensitivity(state: CopilotState) -> Sensitivity:
    """DA-26: classificar incidente como confidential se usa conector REAL (não mock)."""
    if state.evidence and any(
        ev.trust_level in ("system_observed", "simulated") for ev in state.evidence
    ):
        return "confidential"  # Real connector (não mock)
    if state.connector_result and state.connector_result.source != "mock":
        return "confidential"  # Fallback real
    return "public"
```

**Policy routing** (DA-26: l.141-220):

```python
def invoke_via_gateway(prompt: str, state: CopilotState) -> tuple[str, Usage]):
    """DA-26: TODO LLM passa por aqui — nunca invoke_with_hybrid_fallback direto."""

    sensitivity = classify_sensitivity(state)
    locality = get_provider_locality(settings.llm_model)  # "local" ou "cloud"

    # DA-43: soberania de dados
    if sensitivity == "confidential" and locality == "cloud":
        raise PolicyViolationError(
            "Dado confidencial NUNCA pode ir para cloud (DA-26/DA-43). "
            f"Modelo '{settings.llm_model}' aponta para environment cloud."
        )

    # Circuit breaker (DA-41)
    if circuit_breaker.is_open(state.connector_source_system, settings.llm_gateway_circuit_cooldown_seconds):
        raise CircuitOpenError(...)

    # Budget (DA-26)
    if cost_estimate > settings.llm_gateway_max_cost_usd:
        raise BudgetExceededError(...)

    # Chamada real (hybrid fallback: Ollama → cloud)
    return invoke_with_hybrid_fallback(prompt, state)

```

**Consequência prática (DA-43):**
`local_lab` (loopback) pode apontar para internet (`allow_cloud=True`), mas `enterprise_azure` **deve** apontar para loopback (`allow_cloud=False`). Rota `require_loopback=True` + provider cloud = `ConfigurationError` no boot.

---

### 3. Capability Registry FAIL-CLOSED (DA-27)

**DA-27**: Capability Registry (`app/mcp/policy.py`) é **FAIL-CLOSED** — tool sem entrada no registry é negada **por padrão**, nunca por omissão.

#### Arquivo: `app/mcp/policy.py` (l.1-160)

**Estrutura:**

```python
@dataclass(frozen=True)
class ToolPolicy:
    name: str
    risk_level: Literal["low", "medium", "high", "critical"]
    destructive: bool
    scopes_required: tuple[str, ...]
    approval_required: bool
    data_sensitivity: Literal["public", "confidential"]
    timeout_seconds: float = 30.0
    max_retries: int = 0


CAPABILITY_REGISTRY: dict[str, ToolPolicy] = {
    "diagnose_incident": ToolPolicy(
        name="diagnose_incident",
        risk_level="low",
        destructive=False,
        scopes_required=("integration.read",),
        approval_required=False,
        data_sensitivity="confidential",
        timeout_seconds=60.0,
    ),
    "list_connectors": ToolPolicy(
        name="list_connectors",
        risk_level="low",
        destructive=False,
        scopes_required=("integration.read",),
        approval_required=False,
        data_sensitivity="public",
        timeout_seconds=5.0,
    ),
}


def enforce(tool_name: str, context: ExecutionContext) -> ToolPolicy:
    """DA-27: FAIL-CLOSED — tool sem entrada no registry é negada."""
    policy = CAPABILITY_REGISTRY.get(tool_name)
    if policy is None:
        raise PolicyDeniedError(
            f"Tool '{tool_name}' nao esta no Capability Registry - negada "
            "por padrao (fail-closed). Registre a tool em "
            "app/mcp/policy.py::CAPABILITY_REGISTRY antes de expor via @mcp.tool()."
        )

    # Escopo e aprovação verificados
    missing_scopes = set(policy.scopes_required) - context.granted_scopes
    if missing_scopes:
        raise PolicyDeniedError(...)

    if policy.approval_required and not context.approved:
        raise PolicyDeniedError(...)

    return policy
```

**Princípio:**
Toda tool **nova** (especialmente futuras tools de ESCRITA, ex: `"restart_iflow"`, `"close_ticket"`)
deve ganhar uma entrada aqui **ANTES** de ser exposta via `@mcp.tool()` — o próprio `enforce()` que
impede esquecer isso (tool simplesmente seria negada em runtime, não silenciously permitida).

---

### 4. Circuit Breaker Compartilhado (DA-41)

**DA-41**: Backend Redis compartilhado (quando disponível) ou fallback em-memória.

#### Arquivo: `app/circuit_breaker.py` (l.1-293)

**Esquema Redis (hash por chave logica):**

```
cb:<namespace>:<key> → { consecutive_failures: int, opened_at: float|"" }
TTL = cooldown_seconds * 10
```

**Namespace separa os dois usos independentes:**
- `"llm"` → providers de LLM (`gateway.py`)
- `"conn"` → conectores externos (`base.py`)

**Behavior:**
```
closed → (N falhas consecutivas) → open → (cooldown expira) →
deixa a proxima tentativa passar (half-open implicito) →
sucesso reseta para closed, falha reabre.
```

**API pública:**

```python
class CircuitBreaker:
    def is_open(self, key: str, cooldown_seconds: float) -> bool: ...
    def record_success(self, key: str) -> None: ...
    def record_failure(
        self, key: str, failure_threshold: int, cooldown_seconds: float = 300.0
    ) -> None: ...
    def consecutive_failures(self, key: str) -> int: ...
    def reset(self) -> None: ...
```

**Fallback em-memória** (quando Redis indisponível) preserva o princípio "clone e rode"
sem infra obrigatória, mas **não compartilha estado** entre replicas Kyma.

---

### 5. Redação/PII (DA-41/DA-56)

**DA-41 (DA-26 item 4)**: Redação REGEX-based de PII antes de Langfuse e do prompt.

#### Arquivo: `app/redaction.py` (l.1-188)

**Protege:**
- E-mail (RFC-simplificado)
- CPF (formatado ou 11 dígitos com contexto)
- CNPJ (14 dígitos com pontuação)
- Número de IDoc (16 dígitos)
- Bearer tokens (captura token, não cabeçalho inteiro)
- Senhas em JSON/YAML/env-var/XML (chave+valor redigido)

**Defesa em profundidade** (não DLP completo):

```python
def redact_pii_text(text: str) -> str:
    """DA-41: aplicada dentro de sanitize_untrusted_input (app/agent/nodes.py)."""
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _CPF_FORMATTED_RE.sub("[REDACTED_CPF]", text)
    text = _CPF_CONTEXT_RE.sub(_redact_cpf_context, text)
    text = _CNPJ_RE.sub("[REDACTED_CNPJ]", text)
    text = _IDOC_NUMBER_RE.sub("[REDACTED_IDOC]", text)
    text = _BEARER_TOKEN_RE.sub("[REDACTED_TOKEN]", text)
    text = _PASSWORD_JSON_RE.sub(r"\1\"[REDACTED]\"", text)
    text = _PASSWORD_JSON_SINGLE_RE.sub(r"\1'[REDACTED]'", text)
    text = _PASSWORD_YAML_RE.sub(r"\1[REDACTED]", text)
    text = _PASSWORD_ENV_RE.sub(r"\1[REDACTED]", text)
    text = _PASSWORD_XML_RE.sub(r"\1[REDACTED]\2", text)
    return text


def redact_pii_deep(data: Any) -> Any:
    """DA-41: passada como mask= na inicialização do client Langfuse."""
    # Aplicada a QUALQUER input/output capturado por @observe (não só texto)
    if isinstance(data, str):
        return redact_pii_text(data)
    if isinstance(data, dict):
        return {k: redact_pii_deep(v) for k, v in data.items()}
    if isinstance(data, list):
        return [redact_pii_deep(v) for v in data]
    return data
```

**Limitação conhecida (DA-26):**
NER/classificador de PII (nomes próprios, endereços, outros formatos)
permanece pendente — até NER/classificador de PII de verdade,
o módulo fecha o gap dos 3 padrões citados (e-mail/CPF/IDoc).

---

### 6. Cifra Fernet de Credenciais em Repouso (DA-47)

**DA-47**: Master key no `.env` (`LLM_CREDENTIALS_MASTER_KEY`), criptografia com Fernet.

#### Arquivo: `app/admin/crypto.py` (l.1-92)

**Chave no `.env`:**
```bash
LLM_CREDENTIALS_MASTER_KEY="4g8B8v2eX9mK3pQ5rT7wY1zA4cF6hJ9kM2nP4qR6sU8vW1yZ3cB5eG7iJ0kL2mN"
```

**Geração de chave:**
```python
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**API:**

```python
def encrypt_secret(plaintext: str) -> str:
    """DA-47: cifra + devolve token Fernet."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str:
    """DA-47: decifra token Fernet (raise ConfigurationError se master key errada)."""
    return _get_fernet().decrypt(token.encode()).decode()
```

**Consequência:**
- Master key **ausente/invalida** = `ConfigurationError` explícito **no boot**
- NUNCA gerar key nova em runtime (tornaria indecifrável tudo o que já está no banco)
- `mask_secret` (exibição) é determinística e nunca expõe o texto claro (prefixo 4 + `****` + sufixo 4)

---

### 7. Metering de Tokens Reais (DA-48)

**DA-48**: Metering de tokens **reais** (usage_metadata, não estimativa) persistido best-effort.

#### Arquivo: `app/admin/metering.py` + `app/llm/gateway.py` (DA-48: l.170-230)

**Gateway grava after invocation:**

```python
def invoke_via_gateway(prompt: str, state: CopilotState) -> tuple[str, Usage]:
    response = await llm_client.invoke(...)

    # DA-48: usage real (não estimativa)
    usage = response.usage  # type: Usage  # Ex: {"input_tokens": 150, "output_tokens": 42}

    # Best-effort (redis_url opcional)
    if settings.redis_url:
        await record_usage_observed(
            user_id="system",  # ou user_id real se autenticado
            origin=resolve_provider_origin(state.connector_source_system),
            model=settings.llm_model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

    return response.content, usage
```

**Persistência** (DA-48: `app/admin/metering.py:1-180`):

```python
async def record_usage_observed(...) -> None:
    """DA-48: persistir best-effort (no Redis ou PostgreSQL)."""
    if settings.redis_url:
        json_data = json.dumps({
            "user_id": user_id,
            "origin": origin,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "timestamp": time.time(),
        })
        await redis_client.lpush("iic:metering", json_data)
        await redis_client.ltrim("iic:metering", 0, 9999)  # Top 10k
    else:
        # Fallback: log estruturado
        logger.info(
            "[metering] usage observed",
            extra={"user_id": user_id, "origin": origin, ...},
        )
```

**Dashboard Grafana** (DA-16): queries em Postgres (DA-35).

---

## Contratos

### 1. PolicyViolationError (DA-26/DA-43)

Levantada quando dado confidencial tenta ir para cloud:

```python
raise PolicyViolationError(
    "Dado confidencial NUNCA pode ir para cloud (DA-26/DA-43). "
    f"Modelo '{settings.llm_model}' aponta para environment cloud."
)
```

### 2. PolicyDeniedError (DA-27)

Levantada quando tool ausente no Capability Registry ou escopo/aprovação insuficientes:

```python
raise PolicyDeniedError(
    f"Tool '{tool_name}' nao esta no Capability Registry - negada "
    "por padrao (fail-closed). Registre a tool em "
    "app/mcp/policy.py::CAPABILITY_REGISTRY antes de expor via @mcp.tool()."
)
```

### 3. CircuitOpenError (DA-41)

Levantada quando circuit breaker está aberto (cooldown não expirou):

```python
raise CircuitOpenError(
    f"Circuit breaker open para provider '{provider}' cooldown={cooldown_seconds}s"
)
```

### 4. Policy de autenticação

| Transporte | Header | Fonte |
|---|---|---|
| REST (Events webhook) | `X-Event-Mesh-Api-Key` | `settings.api_key` |
| A2A JSON-RPC 2.0 | `X-A2A-Api-Key` | `settings.a2a_api_key` |
| MCP | `X-API-Key` | `settings.api_key` |

---

## Exercícios

### Exercício 1: PolicyViolationError no gateway

**Pergunta:** Qual é o caminho completo de `PolicyViolationError` no gateway quando `sensitivity="confidential"` e `locality="cloud"`? (DA-26/DA-43)

**Solução:**

1. `invoke_via_gateway()` (`app/llm/gateway.py:128-220`) chama `classify_sensitivity(state)` → returns `"confidential"`
2. `get_provider_locality(settings.llm_model)` → returns `"cloud"` (ollama → `"local"`, openai/azure → `"cloud"`)
3. Se `sensitivity == "confidential" and locality == "cloud"` → `raise PolicyViolationError(...)`
4. A exceção não é tratada no gateway → propaga até o handler HTTP (403 ou 500 dependendo do endpoint)

**Invariante-crítica:** Dado confidencial **nunca** pode ir para cloud — nem como fallback.
`strict` mode bloqueia explicitamente essa rota.


### Exercício 2: Fail-closed no Capability Registry

**Pergunta:** Por que o Capability Registry (`app/mcp/policy.py`) é FAIL-CLOSED, e qual é a consequência prática
de esquecer de registrar uma nova tool?

**Solução:**

- **Design:** Uma terceira revisão arquitetural externa apontou que faltava, antes de tools de ESCRITA,
  um modelo de risco por ferramenta — hoje só existe autenticação de TRANSPORTE, sem distinção entre
  "consultar" e "reiniciar um iFlow" (DA-27: l.1-5).
- **Fail-closed:** Se `CAPABILITY_REGISTRY.get(tool_name)` for `None`, `enforce()` levanta `PolicyDeniedError`,
  **nunca** permite por omissão.
- **Consequência:** Se você esquecer de registrar uma nova tool em `CAPABILITY_REGISTRY`, ela simplesmente
  sera negada em runtime com erro de policy, **não silenciosamente permitida**.

**Invariante-crítica:** Toda tool NOVA (especialmente futuras tools de ESCRITA) DEVE ganhar uma entrada
em `app/mcp/policy.py::CAPABILITY_REGISTRY` ANTES de ser exposta via `@mcp.tool()`.


### Exercício 3: Redação PII em Langfuse

**Pergunta:** Como a redação PII (`app/redaction.py`) cobre tanto o prompt quanto o Langfuse, mesmo que
o `@observe` capture o `CopilotState` inteiro?

**Solução:**

- **Defense in depth** (DA-41: l.13-22):
  - `redact_pii_text()` → usada em `sanitize_untrusted_input()` (`app/agent/nodes.py:1-128`),
    cobre "antes do prompt" (texto colado pelo usuário)
  - `redact_pii_deep(data)` → passada como `mask=` na inicialização do client Langfuse,
    cobre "antes do Langfuse": aplicada pelo SDK a QUALQUER input/output capturado por `@observe`,
    não só texto sanitizado manualmente.
- **Exemplo:** Se um usuário colar um CPF no prompt (`"meu CPF é 123.456.789-01"`), ele será redigido
  antes de ser enviado ao LLM **e** antes de ser capturado por Langfuse (`mask=` faz isso automaticamente).

**Limitação conhecida:** NER/classificador de PII (nomes próprios, endereços) permanece pendente.


### Exercício 4: Redis vs em-memória no circuit breaker

**Pergunta:** Como o circuit breaker (`app/circuit_breaker.py`) decide usar Redis ou em-memória?
Quais as implicações de cada opção?

**Solução:**

- **Redis disponible (REDIS_URL configurada e Ping succeed):** Estado distribuido entre replicas (Kyma).
  - `is_open()` → `redis.call('HGET', key, 'opened_at')`
  - `record_failure()` → Lua script para incremento atomico (evita race condition em replicas > 1)
  - `record_success()` → `redis.call('DEL', key)`
- **Redis indisponível (REDIS_URL vazio ou Redis fora do ar):** Fallback para em-memória (preserva
  "clone e rode" sem infra obrigatória).
  - `is_open()` → busca em `self._states: dict[str, _CircuitBreakerState]`
  - `record_failure()` → `state.consecutive_failures += 1`
  - **Sem atomicidade:** Em deploys Kyma com replicas > 1, cada pod tem seu proprio estado,
    portanto replicas podem abrir/fechar o circuito independentemente (sem visibilidade cruzada).

**Invariante-crítica:** Redis compartilhado é necessário para deploys multi-pod; fallback em-memória
preserva "clone e rode".


### Exercício 5: Senha PBKDF2 vs Fernet

**Pergunta:**Qual a diferença entre o esquema de senha PBKDF2 (`app/auth.py`) e o de credenciais
Fernet (`app/admin/crypto.py`) no projeto?

**Solução:**

| Característica | PBKDF2-SHA256 (`app/auth.py`) | Fernet (`app/admin/crypto.py`) |
|---|---|---|
| Objeto | Senhas de login (usuario+senha) | Credenciais de provedores (API keys) |
| Uso | Comparação (verify) | Cifra/decifra (encrypt/decrypt) |
| Iterações | 600k (OWASP 2023) | N/A (AES-128-CBC + HMAC-SHA256) |
| Master key | Senha fixa no `.env` | `LLM_CREDENTIALS_MASTER_KEY` no `.env` |
| Gerar chave | `hash_password(password)` | `Fernet.generate_key()` |
| Erro fatal | `WEB_UI_USERS` ausente = 401 SEMPRE | Master key ausente/invalida = `ConfigurationError` no boot |
| Reversibilidade | Irreversível (hash) | Reversível (decifrar com master key) |

**Invariante-crítica:** Master key NUNCA é gerada em runtime (tornaria indecifrável tudo o que
ja está no banco); senhas PBKDF2 nunca são comparadas com `==`, apenas com `secrets.compare_digest`.


## Invariantes Críticas

### 1. **Soberania de dados é inegociável** (DA-26/DA-43)

Dado confidencial (`system_observed`/`simulated`) **nunca** pode ir para cloud, mesmo como fallback.
`strict` mode bloqueia explicitamente essa rota.

### 2. **Capability Registry é FAIL-CLOSED** (DA-27)

Tool sem entrada no registry é negada **por padrão**, nunca por omissão. Toda tool nova
(especialmente futuras tools de ESCRITA) DEVE ganhar uma entrada ANTES de ser exposta.

### 3. **Circuit breaker compartilhado entre replicas** (DA-41)

Redis compartilhado é necessário para deploys multi-pod; fallback em-memória preserva
"clone e rode" sem infra obrigatória (mas **não** compartilha estado entre replicas).

### 4. **Redação PII é defense in depth** (DA-41)

Dois pontos de uso: `redact_pii_text()` (antes do prompt) e `redact_pii_deep(data)` (antes do Langfuse),
cobrindo tanto texto colado quanto `@observe(input, output)`.

### 5. **Master key Fernet é fixa** (DA-47)

Master key ausente/invalida = `ConfigurationError` no boot. NUNCA gerar key nova em runtime
(torna indecifrável tudo o que já está no banco).

### 6. **Comparações secretas usam `secrets.compare_digest`** (DA-54)

Nenhuma comparação de senha/token usa `==` ou `is`. `secrets.compare_digest()` (const-time,
não vulnerável a timing attack).

### 7. **Circuit breaker é atomico no Redis** (DA-41)

Lua script (A-08) garante que incremento e abertura do circuito sejam operações atomicas
(evita race condition em deploys com N replicas).

## Limitações

### 1. **NER/classificador de PII** (DA-26/DA-41)

Permanece pendente. Até então, só regex-based de e-mail/CPF/IDoc/CNPJ/bearer/senhas.

### 2. **Multitenancy não implementada** (DA-26/DA-41)

Projeto ainda e single-tenant — autenticação por chave única (`api_key`/`a2a_api_key`), sem
tenant isolation.

### 3. **Circuit breaker em-memória não e compartilhado** (DA-41)

Fallback em-memória (quando Redis indisponível) preserva "clone e rode" mas **não** compartilha
estado entre replicas Kyma (cada pod tem seu próprio estado).

### 4. **Escopo MCP por chave única** (DA-27)

Hoje ha uma unica `X-API-Key` (settings.api_key) compartilhada por todo o servidor MCP —
DEFAULT_EXECUTION_CONTEXT reflete isso (todo caller autenticado recebe os MESMOS scopes de leitura).
Multiplas chaves com scopes diferentes exigiria OAuth2/TokenVerifier (sobre-engenharia para o estagio atual).

### 5. **Sessão cookie não vale para superfícies so-de-maquina** (DA-54)

Cookies de browser **não** abrem MCP (`/mcp`), A2A (`/a2a`), Event Mesh (`/events/incident`) ou `/admin`.
Essas superfícies continuam exigindo suas chaves dedicadas.

### 6. **Redis best-effort no metering** (DA-48)

`record_usage_observed` falha silenciosamente se Redis fora do ar (só log estruturado),
não bloqueia a chamada LLM.

## Referências rápidas

### Arquivos-chave

| Arquivo | DA | O que é |
|---|---|---|
| `app/auth.py` | DA-54 | Login de sessão (cookie HMAC, PBKDF2-SHA256, `web_users`) |
| `app/main.py` (l.220-694) | DA-18/DA-19/DA-23/DA-54 | `_ensure_api_keys_configured()`, autenticação, endpoints `/mcp`, `/a2a`, `/events/incident` |
| `app/llm/gateway.py` | DA-26/DA-41/DA-43 | AI Gateway (policy, circuit breaker, budget, audit log, sovereign routing) |
| `app/mcp/policy.py` | DA-27 | Capability Registry (`CAPABILITY_REGISTRY`, `enforce()`, FAIL-CLOSED) |
| `app/circuit_breaker.py` | DA-41 | Redis + fallback em-memória (atomico com Lua script A-08) |
| `app/redaction.py` | DA-41/DA-47/DA-54 | Redação PII (e-mail/CPF/IDoc/CNPJ/bearer/senhas) |
| `app/admin/crypto.py` | DA-47 | Fernet (`encrypt_secret`, `decrypt_secret`, master key no `.env`) |
| `app/admin/metering.py` | DA-48 | Metering de tokens reais (usage_metadata, best-effort) |

### Endpoints que exigem chaves dedicadas

| Endpoint | Header | DA |
|---|---|---|
| `POST /diagnose` | `X-API-Key` | DA-18/DA-54 (também cookie) |
| `POST /events/incident` | `X-Event-Mesh-Api-Key` | DA-23/DA-18 |
| `POST /a2a/message/send` | `X-A2A-Api-Key` | DA-14/DA-18 |
| `GET/POST /mcp/*` | `X-API-Key` | DA-19/DA-18 |

### DAs abordadas

| DA | O que é |
|---|---|
| DA-18 | Autenticação por chave (X-API-Key obrigatória em /diagnose e /a2a) |
| DA-26 | AI Gateway (policy + circuit breaker + budget + audit log) |
| DA-27 | Capability Registry FAIL-CLOSED |
| DA-41 | Circuit breaker com backend Redis (fallback em-memória) |
| DA-43 | Soberania de dados por origin real (fail-closed) |
| DA-47 | Credenciais cifradas em repouso com Fernet |
| DA-48 | Metering de tokens REAIS (persistido best-effort) |
| DA-54 | Login de sessão para a UI web (cookie HMAC, PBKDF2-SHA256) |

### DAs relacionadas

| DA | O que é |
|---|---|
| DA-2 | `seed=42` obrigatório para determinismo Ollama |
| DA-14 | Agent2Agent (JSON-RPC 2.0) |
| DA-19 | Servidor MCP (capability catalog) |
| DA-23 | Event Mesh via webhook CloudEvents |
| DA-40 | Migração aiormq → python-qpid-proton (AMQP 1.0) |
| DA-57 | Web search sources (configuração, fail-closed) |

## Comandos de validação

### 1. Gerar hash de senha PBKDF2 (para `WEB_UI_USERS`):

```bash
uv run python -c "from app.auth import hash_password; print(hash_password('sua senha'))"
```

### 2. Gerar Fernet master key (para `LLM_CREDENTIALS_MASTER_KEY`):

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 3. Validar keys geradas no startup (log sem WARNING = chaves fixadas no `.env`):

```bash
# Sem .env fixado → WARNING nos logs:
uv run uvicorn app.main:app --reload 2>&1 | grep -E "(API_KEY|A2A_API_KEY) gerada"
```

### 4. Testar_policyViolationError (gatilho: dados confidenciais + provider cloud):

```bash
# Configurar o .env para usar provider cloud (DA-43):
# LLM_PROVIDER=openai
# LLM_OPENAI_API_KEY=sua-key
# DATA_SOVEREIGNTY_MODE=strict

uv run python -c "
from app.llm.gateway import classify_sensitivity
from app.models import CopilotState
from app.agent.state import CopilotState as CopilotStateType

state = CopilotStateType(
    connector_source_system='mock',
    evidence=[
        {'trust_level': 'system_observed', 'matched_source': 'OData client real', 'data': 'x', 'depth': 1}
    ]
)
assert classify_sensitivity(state) == 'confidential'  # Real connector
"
```

## DAs

### DA-18: Autenticação por chave

**Problema:** Credencial de BORDA (pensada para maquina-a-maquina) não pode ser reusada pela UI web.

**Solução:** Duas vias de auth em `/diagnose`:
- maquina: header `X-API-Key`, exatamente como antes (DA-18 intacto);
- humano: POST `/auth/login` (usuario+senha) → cookie de sessão HttpOnly, SameSite=Strict, assinado com HMAC-SHA256.

**Limitações:** Superfícies so-de-maquina (MCP, A2A, Event Mesh, `/admin`) NÃO aceitam cookie de sessão.

---

### DA-26: AI Gateway v1

**Problema:** LLM Gateway existente era só Provider Factory / Abstraction Layer — faltava policy,
model routing por sensibilidade, budget/custo, circuit breaker e audit log.

**Solução:** Camada central por onde **TODA** chamada LLM do Copilot passa (`app/agent/nodes.py` chama
`solve_via_gateway()`, não `invoke_with_hybrid_fallback` direto), com:

1. Data Classification + Policy Routing: confidencial → NUNCA provider cloud;
2. Circuit Breaker: após N falhas consecutivas, cooldown;
3. Budget: estimativa de custo rejeita se ultrapassar thresholds;
4. Audit log: linha estruturada por tentativa de chamada.

**Limitações:** IAM/auth (ja resolvido na borda HTTP), PII/DLP de verdade (NER/classificador permanece pendente),
tenant isolation (single-tenant), circuit breaker compartilhado entre replicas (in-memory, não compartilhado).

---

### DA-27: Capability Registry FAIL-CLOSED

**Problema:** Falta modelo de risco por ferramenta — hoje so existe autenticação de TRANSPORTE, sem distinção entre
"consultar" e "reiniciar um iFlow".

**Solução:** `CAPABILITY_REGISTRY` com `ToolPolicy` por tool (risk_level, destructive, scopes_required,
approval_required, data_sensitivity), e `enforce()` que é FAIL-CLOSED (tool sem entrada é negada).

**Limitações:** Granularidade de scope por CHAVE de API (hoje uma unica `X-API-Key` concede Todos os scopes),
fluxo de aprovacao humana de verdade (campo `approved` existe, mas mecanismo não implementado).

---

### DA-41: Circuit breaker compartilhado

**Problema:** Estado em-memoria, por processo — incompativel com deploy Kyma com replicas > 1.

**Solução:** Backend Redis compartilhado (quando disponível), fallback para em-memória (preserva
"clone e rode").

**Limitações:** Redis compartilhado necessário para deploys multi-pod; fallback em-memória não
compartilha estado entre replicas.

---

### DA-43: Soberania de dados por origin real (fail-closed)

**Problema:** Setup default de Hybrid Inference (ollama + fallback cloud) faria um incidente com
dado real de producao SAP vazar para provider externo se Ollama estiver fora do ar.

**Solução:** `sensitivity = classify_sensitivity(state)` (confidencial se `system_observed`/`simulated`),
`locality = get_provider_locality(model)` (local se `ollama`, cloud se `openai`/`azure_openai`),
`if sensitivity == "confidential" and locality == "cloud" → PolicyViolationError`.

**Limitações:** NER/classificador de PII permanece pendente; multitenancy não implementada.

---

### DA-47: Credenciais cifradas em repouso com Fernet

**Problema:** Credenciais deprovedores (API keys) gravadas no banco em texto claro.

**Solução:** Master key no `.env` (`LLM_CREDENTIALS_MASTER_KEY`), criptografia com Fernet (`aes-128-cbc + hmac-sha256`).

**Limitações:** Master key NUNCA é gerada em runtime (torna indecifrável tudo o que já está no banco);
`mask_secret` (exibição) é determinística e nunca expõe o texto claro.

---

### DA-48: Metering de tokens reais (persistido best-effort)

**Problema:** Metering estimado (tokens) em vez de tokens reais (usage_metadata).

**Solução:** `record_usage_observed(user_id, origin, model, input_tokens, output_tokens)` após each
LLM invocation (DAO + Redis best-effort, fallback log estruturado).

**Limitações:** Best-effort (Redis fora do ar = log, não bloqueia LLM).

---

### DA-54: Login de sessão para a UI web

**Problema:** UI web reusava `X-API-Key` de infra, jogando um segredo do servidor na mao do usuario final.

**Solução:** POST `/auth/login` (usuario+senha) → cookie de sessão HttpOnly, SameSite=Strict,
assinado com HMAC-SHA256, verificação PBKDF2-SHA256 (600k iterações).

**Limitações:** Cookie de browser NÃO abre MCP (`/mcp`), A2A (`/a2a`), Event Mesh (`/events/incident`) ou `/admin`.
`SESSION_SECRET` vazio gera segredo efemero (WARNING no log); `WEB_UI_USERS` ausente = 401 SEMPRE.

---

### DA-57: Web search sources (configuração, fail-closed)

**Problema:** Duas mapas literais de `app/agent/nodes.py` (approved) não eram escaláveis e tinham
comportamento "approved" que era no-op (falava-se em "fail-closed" mas não era).

**Solução:** `web_search_sources` (uma linha por interface_type) substitui os dois mapas;
`WEB_SEARCH_POLICY=approved` exige linha habilitada — **fail-closed, sem fallback em código**.

**Limitações:** Falha de banco também é `None`, não exceção (fallback opcional do RAG).
