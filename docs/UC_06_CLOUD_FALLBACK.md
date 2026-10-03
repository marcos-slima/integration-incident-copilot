# Use Case 6: Cloud Fallback (Ollama Offline)

**Contexto:** Ollama local indisponível → fallback para OpenAI/Azure (DA-20/26)

## Fluxo Híbrido (Ollama → Cloud)

### 1. Entry Point (app/llm/gateway.py)

```python
def invoke_via_gateway(
    build_and_invoke: Callable[[LLMClient], dict],
    state: CopilotState,
    prompt_text: str,
    model_name: str
) -> tuple[dict, str]:
    # DA-26: Policy de roteamento
    policy = resolve_policy(state)
    if policy == "strict" and policy.cloud_allowed:
        raise PermissionError("Cloud strictly forbidden for this origin")

    # Estimativa de custo (budget enforcement)
    token_estimate = estimate_tokens(prompt_text)
    if not check_budget(token_estimate):
        raise BudgetExceeded()

    # Hybrid Inference: tenta Ollama first (local)
    llm_client = get_llm_client("Ollama")
    try:
        result = build_and_invoke(llm_client)
        return result, "ollama"
    except TRANSPORT_FAILURE_EXCEPTIONS:
        # DA-26: Circuit breaker
        cb = CircuitBreaker("llm_ollama", fail_max=5, timeout=60)
        if cb.is_open():
            raise ServiceUnavailable("Ollama circuit is open")

        # Fallback cloud (OpenAI/Azure)
        cloud_client = get_llm_client("OpenAI")
        return build_and_invoke(cloud_client), "openai"
```

### 2. Ollama Connector (app/llm/factory.py)

```python
# app/llm/factory.py::create_ollama_client()
def create_ollama_client() -> LLMClient:
    return LLMClient(
        base_url="http://localhost:11434",
        model=settings.llm_model,
        timeout=30,
        max_retries=2,
        backoff_exponential=True  # DA-30
    )
```

**Exceções (TRANSPORT_FAILURE_EXCEPTIONS):**
- `requests.exceptions.ConnectionRefusedError`
- `requests.exceptions.Timeout`
- `httpx.RemoteProtocolError`
- `ConnectionError`

### 3. Backoff Exponencial (DA-30)

```python
# app/llm/factory.py
def _backoff_retry(func, max_retries=2):
    for attempt in range(max_retries + 1):
        try:
            return func()
        except TRANSPORT_FAILURE_EXCEPTIONS as exc:
            if attempt == max_retries:
                raise
            delay = (2 ** attempt)  # 1s, 2s, 4s...
            time.sleep(delay)
```

### 4. Cloud Fallback (OpenAI/Azure)

```python
# app/llm/factory.py::create_cloud_client()
def create_cloud_client provider="openai"
    if provider == "openai":
        return OpenAI(
            api_key=settings.openai_api_key,
            model=settings.cloud_model or "gpt-4o-mini"
        )
    elif provider == "azure":
        return AzureOpenAI(
            api_key=settings.azure_api_key,
            api_version="2024-02-01",
            azure_endpoint=settings.azure_endpoint,
            model=settings.azure_deployment
        )
```

### 5. DA-20: Hybrid Inference Logic

**Padrão:**
```python
def invoke_with_hybrid_fallback(prompt: str, model_name: str) -> str:
    # 1. Tenta Ollama
    try:
        return ollama_generate(prompt, model_name)
    except ConnectionError:
        # 2. Fallback cloud
        return cloud_generate(prompt, settings.cloud_model or "gpt-4o-mini")
```

**Implementação real (nodes.py:_run_diagnosis_agent):**
```python
react_result, llm_provider_used = invoke_via_gateway(
    _build_and_invoke, state=state, prompt_text=prompt, model_name=model_name
)
```

## Exemplo de Workflow

### Caso 1: Ollama Online
1. `POST /diagnose` → `llm_provider_used = "ollama"`
2. Latência: ~2s (local)
3. Token count: registrado em `metering` (DA-48)

### Caso 2: Ollama Offline
1. `POST /diagnose` → tenta Ollama → `ConnectionRefusedError`
2. Circuit breaker check → `close` (reiniciado há 5 min)
3. Tenta OpenAI → sucesso
4. `llm_provider_used = "openai"`
5. Latência: ~5s (network + cloud)

### Caso 3: Ambos Offline
1. `POST /diagnose` → Ollama → fail
2. OpenAI → fail (API key inválida)
3. `TRANSPORT_FAILURE_EXCEPTION` sobe para FastAPI
4. HTTP 503 + `{"error": "LLM services unavailable"}`

## DA-43: Policy de Soberania

```python
# app/llm/gateway.py
def resolve_policy(state: CopilotState) -> Policy:
    origin = state.get("origin", "unknown")
    policy = origin_to_policy.get(origin, Policy(strict=True, cloud_allowed=False))
    return policy
```

**Exemplo:**
- `origin="local_lab"` → `strict=False, cloud_allowed=True`
- `origin="enterprise_azure"` → `strict=True, cloud_allowed=False` (data local)

## DA-48: Metering Real

```python
# app/llm/gateway.py::record_usage()
def record_usage(provider: str, prompt_tokens: int, completion_tokens: int):
    # grava em PostgreSQL (best-effort, não bloqueia response)
    execute_async(
        "INSERT INTO metering (provider, prompt_tokens, completion_tokens, timestamp) VALUES (...)"
    )
```

**Exemplo de gravação:**
```json
{
  "provider": "openai",
  "prompt_tokens": 1250,
  "completion_tokens": 180,
  "timestamp": "2026-10-02T10:15:30Z"
}
```
