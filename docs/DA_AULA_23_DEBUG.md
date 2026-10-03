# Aula 23: Debug e Observabilidade na Execução do Agent

**Status:** *Provisório - Em elaboração*
**Última atualização:** 2026-10-03
**Nível:** Intermediário
**Duração estimada:** 2–3 horas

---

## Onde Vive

Este documento está em `docs/DA_AULA_23_DEBUG.md`,内嵌 no índice de Decisões de Arquitetura (`README.md` seção **Decisões de Arquitetura** como DA-60).

---

## Objetivo

Criar um guia prático para **debugar a execução real** do Integration Incident Copilot, desde a recepção de uma requisição HTTP até a emissão da resposta final. O foco é entender **como os módulos colaboram** durante a execução e **identificar pontos críticos** onde decisões são tomadas.

Ao final, você será capaz de:

1. Configurar ambiente de debug com VS Code
2. Estabelecer pontos de parada estratégicos nos nós do LangGraph
3. Interpretar o estado (`CopilotState`) em cada etapa
4. Correlacionar framework (LangGraph, FastAPI), módulo (agent, connectors, llm) e produto (Ollama, Qdrant)

---

## Pré-requisitos

### 1. Ambiente de Desenvolvimento

```bash
# Certifique-se de ter o Python ≥3.12 e o virtualenv
python --version  # ≥3.12 exigido
```

### 2. Infraestrutura Local

Executar os serviços required (`postgres`, `qdrant`, `ollama`) no Host do desenvolvedor:

```bash
# Perfil observability (postgres, qdrant, Grafana, Redis)
docker compose --profile observability up -d postgres qdrant grafana redis
```

Verificar serviços:

```bash
curl -sf http://127.0.0.1:6333/ping      # Qdrant
curl -sf https://127.0.0.1:11434/v1/   # Ollama (host nativo)
curl -sf http://127.0.0.1:5432          # Postgres (via psql ou health endpoint)
```

### 3. Chaves de API Fixas (non-ephemeral)

Para debug com `/diagnose`, o `X-API-Key` deve ser **fixo** no `.env`:

```bash
# Editar .env e fixar chaves:
API_KEY="debug-key-$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
ADMIN_API_KEY="admin-$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

> **Nota:** Se usar `REQUIRE_AUTH=false`, as chaves são geradas no startup e mudam a cada reinício → inconveniente para debug repetido.

### 4. IDE Visual Studio Code

**Extensões recommandadas:**

- **Python** (Microsoft)
- **Pylance** (Microsoft)
- **VS Code SSH** (se debugar via remote SSH)
- **ESLint + TypeScript** (se debugar frontend)

**Launch configurations** (já existem em `.vscode/launch.json`):

```jsonc
{
  "name": "Debug: graph.py (caso IDoc travado)",
  "type": "python",
  "request": "launch",
  "module": "app.agent.graph",
  "env": { "API_KEY": "debug-key-..." },
  "args": ["--title", "IDoc stuck in SAP", "--connector_source_system", "sap-erp"]
}
```

### 5. Conhecimento Prévio

- LangGraph: Grafo LangChain, nodes, edges, conditional edges
- FastAPI: Dependency injection, Middleware, `State` ASGI
- Pydantic: `BaseModel`, Field validation, `TypedDict`
- async/await: SQLAlchemy async, httpx async, Redis async

---

## Arquitetura e Correlação de Módulos

### 1. Fluxo de Dados (High Level)

```
HTTP Request (/diagnose)
       ↓
FastAPI (app/main.py) → RequireApiKeyMiddleware
       ↓
DiagnoseRequest (Pydantic) → validation
       ↓
CopilotState (TypedDict) → entrada no LangGraph
       ↓
Graph: supervisor_node (DA-22) → determinístico, sem LLM
       ↓
{ sap_diagnosis_node | saas_diagnosis_node | generic_diagnosis_node }
       ↓
       ├─→ connector_node (fetch identifier) → ConnectorResult
       ├─→ retrieve_node (Qdrant RAG) → chunks + evidence
       ├─→ rules_node (DA-33) → 21 regras determinísticas
       ├─→ llm_node (Ollama/local gateway) → prompt + schema
       └─→ report_node (DiagnosisResponse) → structured output
       ↓
HTTP Response (JSON)
```

### 2. Módulo × Framework × Produto

| Módulo | Framework | Produto | Função no Debug |
|--------|-----------|---------|-----------------|
| `app/main.py` | FastAPI + Pydantic | — | Endpoint `/diagnose`, middleware, lifespan |
| `app/agent/graph.py` | LangGraph | LangChain | Grafo, nós, arestas, estado `CopilotState` |
| `app/agent/nodes.py` | LangChain (tool-calling) | Ollama → OpenAI/Azure | `connector_node`, `retrieve_node`, `llm_node` |
| `app/connectors/` | httpx, paramiko, oauthlib | SAP OData/RFC, ServiceNow, etc. | `fetch(identifier)` → `ConnectorResult` |
| `app/llm/factory.py` | langchain-openai, langchain-ollama | Ollama (host), OpenAI, Azure | hybrid fallback, seed=42, circuit breaker |
| `app/rag/retriever.py` | langchain-qdrant, sentence-transformers | Qdrant (dense+sparse BM25 + RRF) | busca híbrida, reranker (Da-29) |
| `app/agent/rules.py` | Pydantic + re | — | 21 regras determinísticas (DA-33) |
| `app/agent/escalation.py` | Pydantic | — | signal tier (DA-44), thresholds |

### 3. Correlação de Produtos

- **Ollama** (`localhost:11434`): modelo canônico `qwen3-coder-next:latest`
- **Qdrant** (`localhost:6333`): RAG dense+sparse + reranker
- **PostgreSQL** (`localhost:5432`): `incidents`, `web_users`, `system_contracts`
- **Redis** (`localhost:6379`): RQ (workers), idempotência (SADD)

---

## Padrões de Debug

### 1. Estratégia de Pontos de Parada (Breakpoints)

**Critérios de escolha:**

- **Mudança de contexto:** onde o estado (`CopilotState`) é alterado
- **Transição determinística ↔ LLM:** onde o Rule Engine decide ou cede para o LLM
- **I/O externo:** chamadas a produtos (Ollama, Qdrant, SAP)

### 2. Breakpoints Propostos

#### **BP-1: `app/main.py:230`** → `/diagnose` endpoint handler

```python
# app/main.py:230
@router.post("/diagnose", tags=["diagnosis"], response_model=DiagnosisResponse)
async def diagnose(
    request: IncidentRequest,
    api_key: str = Depends(get_api_key_from_header),
) -> DiagnosisResponse:
    """
    Receive incident description and return structured diagnosis.
    """
    logger.info(
        "Received diagnosis request",
        extra={"connector_source_system": request.connector_source_system},
    )
    return await run_diagnosis(request)
```

**O que aprende:**

- FastAPI recebe request e valida com `IncidentRequest` (Pydantic)
- Middleware `RequireApiKeyMiddleware` passou (senão, 403)
- `api_key` foi extraída e logada (observar em debug)

**Correlação:**

- `IncidentRequest` → Pydantic v2 → validação `connector_type`, `interface_type` (Literal)
- `api_key` → `get_api_key_from_header()` → `Depends()` FastAPI

---

#### **BP-2: `app/agent/graph.py:70`** → `run_diagnosis()`, entrada no LangGraph

```python
# app/agent/graph.py:70
async def run_diagnosis(request: IncidentRequest) -> DiagnosisResponse:
    """
    Execute the full diagnosis pipeline using LangGraph.
    """
    config = {"configurable": {"thread_id": str(uuid4())}}
    inputs = CopilotState(
        description=request.description,
        connector_source_system=request.connector_source_system,
        connector_type=request.connector_type,
        interface_type=request.interface_type,
    )
    graph = build_graph()
    result = await graph.ainvoke(inputs, config=config)
    return DiagnosisResponse(**result)
```

**O que aprende:**

- `CopilotState` é Criado com os campos de `IncidentRequest`
- `config` de LangGraph com `thread_id` único (para tracing)
- `build_graph()` compila o grafo (`add_conditional_edges`, `set_entry_point`)

**Correlação:**

- `CopilotState` (TypedDict) → Pydantic → estado compartilhado do grafo
- `thread_id` → LangGraph tracing → Langfuse (opcional)

---

#### **BP-3: `app/agent/supervisor.py:45`** → `classify_domain()`, roteamento determinístico

```python
# app/agent/supervisor.py:45
def classify_domain(state: CopilotState) -> Literal["sap", "saas", "generic"]:
    """
    Classify the domain of the incident deterministically (no LLM).
    """
    logger.debug("Classifying domain", extra={"description": state.description})

    description_lower = state.description.lower()

    if _is_sap_domain(description_lower):
        return "sap"
    elif _is_saas_domain(description_lower):
        return "saas"
    else:
        return "generic"
```

**O que aprende:**

- **Sem LLM:** toda lógica é determinística (regex, keywords)
- `state.description` é a única fonte de decisão
- Retorna `Literal["sap", "saas", "generic"]` (usado em `add_conditional_edges`)

**Correlação:**

- `state` (CopilotState) → `description` → lower() → regex match
- `Literal["sap", "saas", "generic"]` → LangGraph edge routing

---

#### **BP-4: `app/agent/nodes.py:120`** → `connector_node`, fetch do conector

```python
# app/agent/nodes.py:120
async def connector_node(state: CopilotState) -> CopilotState:
    """
    Invoke the appropriate connector to fetch system information.
    """
    connector_type = state.connector_type
    if connector_type is None:
        raise ValueError("connector_type não definido no estado")

    logger.info("Invoking connector", extra={"connector_type": connector_type})

    connector = get_connector(connector_type)
    result = await connector.fetch(state.connector_source_system)

    return state.update(
        connector_result=result,
        evidence=[],
        is_grounded=False,
        confidence=0.0,
    )
```

**O que aprende:**

- `get_connector(connector_type)` → instância do conector (ex: `ODataConnector`)
- `connector.fetch(identifier)` → chamada real ao produto (SAP OData, ServiceNow, etc.)
- `ConnectorResult` é convertido para `state.update()`

**Correlação:**

- `connectors/__init__.py` → `_REGISTRY` → `get_connector()` → classe concreta
- `connector.fetch()` → httpx/paramiko/oauthlib → produto (Ollama não entra aqui)

---

#### **BP-5: `app/agent/rules.py:50`** → `match_known_error()`, first-hit no Rule Engine

```python
# app/agent/rules.py:50
def match_known_error(description: str) -> tuple[bool, str] | None:
    """
    Try to match the description against known error patterns (DA-33).
    Returns (matched,planation) or None.
    """
    for rule in _RULES:
        if rule.pattern.search(description):
            return (True, rule.explanation)
    return None
```

**O que aprende:**

- Rule Engine **antes de any LLM call** (DA-33)
- Regex patterns (`rule.pattern`) contra `description`
- Retorna `explanation` se match, `None` se fallback para LLM

**Correlação:**

- `_RULES` (lista de 21 rules) → Pydantic `Rule` (pattern: `re.Pattern`)
- `description` (CopilotState) → match → `explanation` → `DiagnosisResponse.diagnosis` sem LLM

---

#### **BP-6: `app/llm/factory.py:90`** → `invoke_with_hybrid_fallback()`, chamada ao LLM

```python
# app/llm/factory.py:90
async def invoke_with_hybrid_fallback(
    prompt: str,
    tools: list[dict],
    model: str,
    seed: int | None = 42,
) -> LLMResult:
    """
    Invoke LLM with hybrid fallback: Ollama (default) → cloud (fallback)
    """
    try:
        logger.debug("Trying Ollama first", extra={"model": model})
        result = await _invoke_ollama(prompt, tools, model, seed)
        return result
    except Exception as err:
        logger.warning("Ollama fallback", extra={"error": str(err)})
        return await _invoke_cloud(prompt, tools, model, seed)
```

**O que aprende:**

- Hybrid fallback (DA-20): Ollama → OpenAI/Azure
- `seed=42` obrigatório (DA-2) → `llm_model` configura se envia `None` ou `42`
- Exception handling → fallback automático

**Correlação:**

- `model` (ex: `qwen3-coder-next:latest`) → Ollama `/api/generate`
- Exception → `_invoke_cloud()` → OpenAI/Azure SDK
- `seed` → `None` (envia para produtos sem suporte) ou `42` (determinismo)

---

#### **BP-7: `app/rag/retriever.py:150`** → `_evidence_admission_score()`, threshold pós-retrieval

```python
# app/rag/retriever.py:150
def _evidence_admission_score(score: float) -> bool:
    """
    Threshold to admit chunk as evidence (DA-25).
    """
    return score >= 0.55
```

**O que aprende:**

- Threshold **pós-reranker** (DA-25): Qdrant → reranker (Da-29) → admission
- `score` (float 0–1) → threshold 0.55 → bool
- chunks reprovados não entram em `state.evidence`

**Correlação:**

- `retriever.py` → Qdrant (dense+sparse) → reranker (CrossEncoder) → admission
- `score` (Da-29) → sigmoid calibration → `evidence_strength` (DA-15)

---

#### **BP-8: `app/agent/nodes.py:280`** → `report_node`, montagem da resposta final

```python
# app/agent/nodes.py:280
def report_node(state: CopilotState) -> CopilotState:
    """
    Assemble the final diagnosis report.
    """
    is_grounded = state.is_grounded
    evidence_strength = state.evidence_strength

    if is_grounded:
        confidence = min(evidence_strength + 0.25, 1.0)
    else:
        confidence = evidence_strength

    return state.update(
        diagnosis=_assemble_diagnosis(state),
        diagnosis_correct=None,  # NULL até verificado
        evidence_strength=round(evidence_strength, 2),
        confidence=round(confidence, 2),
    )
```

**O que aprende:**

- Guardrails em código (DA-3): `evidence_strength + 0.25` → teto 1.0
- `diagnosis_correct=None` (NULL SQL, não autoavaliação LLM)
- `confidence` é deterministicamente calculada

**Correlação:**

- `_assemble_diagnosis()` → Rule Engine `explanation` ou LLM output
- `evidence_strength` (DA-15) → guardrail → `confidence`
- `state.update()` → retorno ao LangGraph → FastAPI response

---

## Exercícios Observáveis

### 1. Setup básico de debug

1. **Start infra:**

```bash
docker compose --profile observability up -d postgres qdrant grafana redis
```

2. **Criar `.env`** (mode nativo):

```bash
cp .env.example .env
# Edit .env:
API_KEY="debug-key-$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
ADMIN_API_KEY="admin-$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
DATABASE_URL="postgresql+asyncpg://iic:iic@127.0.0.1:5432/iic"
QDRANT_URL="http://127.0.0.1:6333"
LLM_URL="http://127.0.0.1:11434"
```

3. **Start servidor com debug:**

```bash
uv run uvicorn app.main:app --reload
```

4. **No VS Code:**

- Abrir `app/main.py:230`
- Inserir breakpoint
- Executar `Debug: FastAPI (uvicorn)`
- Executar requisição: `curl -H "X-API-Key: debug-key-..." http://localhost:8000/diagnose ...`

### 2. Exercício 1: Fluxo completo de um IDoc stuck (SAP)

**Request:**

```bash
curl -s -X POST http://localhost:8000/diagnose \
  -H "X-API-Key: debug-key-..." \
  -H "Content-Type: application/json" \
  -d '{
    "description": "IDoc stuck in SAP ERP, status 51",
    "connector_type": "odata",
    "connector_source_system": "sap-erp"
  }' | jq .
```

**Breakpoints a observar:**

| BP | Arquivo:linha | O que esperar ver |
|---|---|---|
| BP-1 | `app/main.py:230` | Request JSON recebido, `connector_type="odata"` |
| BP-2 | `app/agent/graph.py:70` | `CopilotState` criado, `thread_id` único |
| BP-3 | `app/agent/supervisor.py:45` | `classify_domain()` → `"sap"` |
| BP-4 | `app/agent/nodes.py:120` | `get_connector("odata")`, `fetch("sap-erp")` |
| BP-6 | `app/llm/factory.py:90` | Ollama invocado (`qwen3-coder-next:latest`) |
| BP-8 | `app/agent/nodes.py:280` | `confidence` calculado, `diagnosis` montado |

### 3. Exercício 2: Flow de Rule Engine (sem LLM)

**Request:**

```bash
curl -s -X POST http://localhost:8000/diagnose \
  -H "X-API-Key: debug-key-..." \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Error 51 in IDoc processing: partner system not found",
    "connector_type": "odata",
    "connector_source_system": "sap-erp"
  }' | jq .
```

**Breakpoints:**

| BP | Arquivo:linha | O que esperar ver |
|---|---|---|
| BP-5 | `app/agent/rules.py:50` | `match_known_error()` → `True, "Error 51: partner system not found"` |
| BP-6 | `app/llm/factory.py:90` | **NÃO É ALCANÇADO** (Rule Engine shortcut) |
| BP-8 | `app/agent/nodes.py:280` | `diagnosis` vem de `explanation`, não de LLM |

### 4. Exercício 3: RAG evidence strength

**Request:**

```bash
curl -s -X POST http://localhost:8000/diagnose \
  -H "X-API-Key: debug-key-..." \
  -H "Content-Type: application/json" \
  -d '{
    "description": "OData timeout in SAP ERP",
    "connector_type": "odata",
    "connector_source_system": "sap-erp"
  }' | jq .
```

**Breakpoints:**

| BP | Arquivo:linha | O que esperar ver |
|---|---|---|
| BP-7 | `app/rag/retriever.py:150` | `_evidence_admission_score(0.62)` → `True` (admitido) |
| BP-7 | `app/rag/retriever.py:150` | `_evidence_admission_score(0.45)` → `False` (rejeitado) |
| BP-8 | `app/agent/nodes.py:280` | `evidence_strength` de chunks admitidos → `confidence` |

---

## Invariantes e Limitações

### Invariantes de Debug

1. **`CopilotState` é source of truth** → todos os nós recebem/retornam `CopilotState`
2. **Rule Engine ANTES de any LLM** → nunca chamar `llm_node` se `match_known_error()` hit
3. **Determinístico → LLM** → `supervisor_node` e `rules_node` não usam LLM
4. **`seed=42` sempre** → DA-2, nunca `None` (senão produtos incompatíveis)
5. **Thresholds em código, não em prompt** → DA-3, guardrails em `_apply_confidence_guardrails()`

### Limitações

1. **Debug com Ollama:** se Ollama não estiver rodando, fallback → cloud → custo
2. **Qdrant não estático:** chunks mudam com novas ingestões → scores mudam
3. **Thread-id único:** cada requisição gera novo UUID → tracing distinto
4. **Pydantic validation:** `connector_type` ou `interface_type` inválidos → 422 antes de debugar grafo

---

## Referências

| Referência | Onde |
|---|---|
| DA-1 (RAG top-1) | `retriever.py` |
| DA-2 (`seed=42`) | `factory.py` |
| DA-3 (Guardrails em código) | `nodes.py::_apply_confidence_guardrails()` |
| DA-20 (Hybrid fallback) | `factory.py::invoke_with_hybrid_fallback()` |
| DA-22 (Multi-agent supervisor) | `supervisor.py::classify_domain()` |
| DA-25 (Threshold pós-retrieval) | `retriever.py::_evidence_admission_score()` |
| DA-29 (Reranker在校验) | `retriever.py::RERANKER_MODEL` |
| DA-33 (Rule Engine pré-LLM) | `rules.py::match_known_error()` |
| DA-44 (Escalation signal) | `escalation.py` |
| DA-51 (Quality gates) | `evaluation/gates.py` |
| DA-53 (Prompt versioning) | `prompts.py` |
| DA-57 (Web search policy) | `nodes.py::_web_search_allowed()` |

---

## Checklist de Validação

Antes de considerar este guia completo:

- [ ] Ambiente de debug montado (infra + `.env` + VS Code)
- [ ] Exercício 1 (fluxo completo IDoc stuck) executado
- [ ] Exercício 2 (Rule Engine sem LLM) executado
- [ ] Exercício 3 (RAG evidence strength) executado
- [ ] BP-3 (`classify_domain()`) confirmado determinístico
- [ ] BP-5 (`match_known_error()`) confirmado antes de LLM
- [ ] BP-7 (`_evidence_admission_score()`) confirmado threshold 0.55
- [ ] BP-8 (`report_node()`) confirmado `confidence` deterministicamente calculado

---

**Próximos passos:** After this, proceed to **performance tuning** (módulo 14, `app/metrics.py`, `app/queue.py`) para monitoramento de latência e through-put.
