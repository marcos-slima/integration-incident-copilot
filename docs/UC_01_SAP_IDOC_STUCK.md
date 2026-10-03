# Use Case 1: IDoc Stuck SAP (Fluxo Completo)

**Contexto:** IDoc travado no status 51 (Processamento) → diagnóstico completo com RAG + LLM (DA-33/53)

## Fluxo Completo (HTTP → response)

### 1. Entrada HTTP (app/main.py:100–140)
```python
@app.post("/diagnose")
async def diagnose(request: IncidentRequest):
    """
    Entrada HTTP com X-API-Key obrigatória (DA-18).
    Valida fields obrigatórios (description, interface_type, identifier).
    """
    return await run_diagnosis(request)
```

### 2. LangGraph Entry Point (app/agent/graph.py:75–95)
```python
async def run_diagnosis(request: IncidentRequest) -> DiagnosisResponse:
    """
    LangGraph entry point: supervisor → connector → retrieve → llm → report.
    """
    graph = build_graph()
    result = await graph.ainvoke({
        "description": request.description,
        "interface_type": request.interface_type,
        "identifier": request.identifier,
        "connector_source_system": request.connector_source_system,
        "context": request.context,
    })
    return result["diagnosis"], result["incident_id"]
```

### 3. Supervisor (app/agent/supervisor.py:35–70)
```python
def classify_domain(state: CopilotState) -> CopilotState:
    """Deterministico routing (DA-22): SAP vs generic."""
    interface_type = state.get("interface_type", "")
    description = state.get("description", "")

    _SAP_KEYWORDS = {"idoc", "rfc", "bapi", "odata", "sap", "enterprise", "s/4"}
    _SAAS_KEYWORDS = {"servicenow", "salesforce", "workday", "ariba", "successfactors"}

    if any(k in description.lower() for k in _SAP_KEYWORDS) or interface_type in {"odata", "rfc"}:
        state["agent_domain"] = "sap"
    elif any(k in description.lower() for k in _SAAS_KEYWORDS):
        state["agent_domain"] = "saas"
    else:
        state["agent_domain"] = "generic"

    return state
```

**Resultado:** `agent_domain = "sap"` (IDoc = SAP keyword)

### 4. Connector Node (app/agent/nodes.py:105–160)
```python
async def connector_node(state: CopilotState) -> CopilotState:
    """
    Chama o conector certo (real ou mock) e adiciona contexto.
    """
    interface_type = state["interface_type"]
    identifier = state["identifier"]

    if interface_type == "odata":
        connector = ODataConnector()
        result = await connector.fetch(identifier)
    elif interface_type == "rfc":
        connector = RFCConnector()
        result = await connector.fetch(identifier)
    else:
        connector = create_mock_connector(interface_type)
        result = await connector.fetch(identifier)

    return {
        **state,
        "connector_result": result,
        "context": result.to_context(),
    }
```

**Resultado:** `connector_result` com payload/headers + `is_fallback=False` (real)

### 5. Retrieve Node (app/agent/nodes.py:165–220)
```python
async def retrieve_node(state: CopilotState) -> CopilotState:
    """
    RAG (Qdrant + reranker): busca no incidents_index + reference_library.
    """
    description = state["description"]

    # Híbrido: dense + sparse (BM25)
    dense_results = await qdrant.search_dense(description, top_k=3)
    sparse_results = await qdrant.search_sparse(description, top_k=3)

    # Fusão RRF (Reciprocal Rank Fusion)
    fused = rrf_fusion([dense_results, sparse_results], top_k=3)

    # Reranker (cross-encoder/mmarco-mMiniLMv2-L12-H384-v1)
    reranked = await reranker.rerank(description, fused)

    # Top-1 (DA-1) → contexto passado ao LLM
    top1 = reranked[0] if reranked else None

    return {
        **state,
        "retrieved_context": top1.payload["content"] if top1 else None,
        "retrieved_documents": [r.payload for r in reranked],
        "top_score": reranked[0].score if reranked else 0.0,
    }
```

**Resultado:** `retrieved_context` com chunk mais relevante + `rerank_score = 0.85`

### 6. Graph Enrich Node (app/agent/nodes.py:225–245)
```python
async def graph_enrich_node(state: CopilotState) -> CopilotState:
    """
    GraphRAG opt-in (DA-28): busca histórico relacionado no Neo4j.
    """
    if not settings.graph_rag_enabled:
        return state

    identifier = state["identifier"]

    try:
        history = await neo4j_client.query(
            "MATCH (i:Incident {identifier: $id}) RETURN i.description AS desc ORDER BY i.created_at DESC LIMIT 1",
            {"id": identifier}
        )
        state["graph_history"] = history[0]["desc"] if history else None
    except (DriverError, TransientError):
        state["graph_history"] = None  # DA-21: tolerância a falha Neo4j

    return state
```

**Resultado:** `graph_history = None` (GraphRAG opt-in, desligado por default)

### 7. Rules Node (app/agent/nodes.py:76–128)
```python
def rules_node(state: CopilotState) -> CopilotState:
    """
    Rule engine determinístico (DA-33): match sem LLM se possível.
    """
    if not settings.rule_engine_enabled:
        return state

    description = state["description"]
    match = match_known_error(description)

    if match:
        state["rule_match"] = match
        state["llm_used"] = False
        return state

    return state  # Sem match, proceed para LLM
```

**Resultado:** `match = None` (descrição é "IDoc stuck in status 51", não bate com nenhuma regra)

### 8. LLM Node (app/agent/nodes.py:250–320)
```python
async def llm_node(state: CopilotState) -> CopilotState:
    """
    LLM inference (hybrid fallback, DA-20/26).
    """
    context = state["retrieved_context"]
    description = state["description"]

    # Prompt (DA-53)
    prompt = PromptTemplate.from_template(prompts.DIAGNOSIS_PROMPT)
    chain = prompt | llm | output_parser

    # Invoke (DA-20)
    result = await invoke_via_gateway(
        lambda: chain.invoke({
            "context": context or "Nenhum contexto disponível.",
            "description": description,
        }),
        policy=settings.ai_gateway_policy  # strict / cloud_with_dlp
    )

    # Apply guardrails (DA-3/15)
    result = _apply_confidence_guardrails(state, result)

    return {
        **state,
        "diagnosis": result,
        "llm_used": True,
        "llm_provider_used": result.get("provider", "unknown"),
    }
```

**Resultado (Ollama):**
```python
{
    "probable_root_cause": "Conector RFC ou OData com erro de commit/rollback não tratado",
    "recommended_actions": [
        "Verificar status do IDoc no transaction WE02/WE05",
        "Consultar log do gateway (transaction SMGW)",
        "Verificar credenciais e endpoint do receptor"
    ],
    "evidence_strength": 0.85,  # RAG score
    "model_confidence": 0.92,
    "provider": "ollama",
}
```

### 9. Graph Write Node (app/agent/nodes.py:325–380)
```python
async def graph_write_node(state: CopilotState) -> CopilotState:
    """
    Write to GraphRAG（opt-in）.
    """
    if not settings.graph_rag_enabled or not state.get("incident_id"):
        return state

    try:
        await neo4j_client.execute(
            """
            MERGE (i:Incident {identifier: $identifier})
            SET i.description = $description,
                i.root_cause = $root_cause,
                i.recommended_actions = $actions,
                i.diagnosis_confidence = $confidence,
                i.updated_at = datetime()
            """
            {"identifier": state["identifier"],
             "description": state["description"],
             "root_cause": state["diagnosis"]["probable_root_cause"],
             "actions": state["diagnosis"]["recommended_actions"],
             "confidence": state["diagnosis"]["diagnosis_confidence"],}
        )
    except (DriverError, TransientError):
        pass  # DA-21: tolerância a falha Neo4j

    return state
```

**Resultado:** write skipped (GraphRAG desligado)

### 10. Report Node (app/agent/nodes.py:250–340)
```python
async def report_node(state: CopilotState) -> CopilotState:
    """
    Generate final report (DA-53: prompt_version/prompt_digest na resposta).
    """
    rule_match = state.get("rule_match")
    llm_used = state.get("llm_used", False)

    if rule_match:
        # Rule engine match (DA-53: prompt_version/prompt_digest = null)
        report = generate_report_from_rule(state, rule_match)
    else:
        # LLM result (DA-53: prompt_version/prompt_digest = reais)
        report = generate_report_from_llm(state, llm_used)

    return {
        **state,
        "report": report,
        "prompt_version": state.get("prompt_version", "desconhecido") if llm_used else None,
        "prompt_digest": state.get("prompt_digest", "desconhecido") if llm_used else None,
    }
```

**Response:**
```json
{
    "incident_id": "INC-12345",
    "probable_root_cause": "Conector RFC ou OData com erro de commit/rollback não tratado",
    "recommended_actions": [
        "Verificar status do IDoc no transaction WE02/WE05",
        "Consultar log do gateway (transaction SMGW)",
        "Verificar credenciais e endpoint do receptor"
    ],
    "llm_provider_used": "ollama",
    "prompt_version": "v2.1.0",
    "prompt_digest": "sha256:abc123...",
    "evidence_strength": 0.85,
    "model_confidence": 0.92,
    "diagnosis_confidence": 0.92,
    "llm_used": true,
    "matched_rule": null
}
```

## DA-33: Rule Engine (21 Regras Implementadas)

| Rule ID | Descrição | Confiança |
|---|---|---|
| `IDOC_STUCK` | IDoc stuck status 51 | 0.95 |
| `RFC_DEST_NOT_FOUND` | RFC destination não encontrado | 0.93 |
| `AUTH_FAILED` | Falha de autenticação SAP | 0.92 |
| `NETWORK_TIMEOUT` | Timeout de conexão | 0.90 |
| `ODATA_ERROR` | OData request error | 0.88 |
| `RFC_COMM_FAILURE` | RFC communication failure | 0.89 |
| `BAPI_ERROR` | BAPI return code de erro | 0.87 |
| `SMW0_ERROR` | Error no SAP Office (SMW0) | 0.86 |
| `RFC_REMOTE_ERROR` | Error remoto no servidor RFC | 0.91 |
| `ODATA_METADATA` | $metadata inválido ou alterado | 0.92 |
| `RFC_TIMEOUT` | RFC timeout | 0.89 |
| `AUTH_TOKEN_EXPIRED` | Token OAuth expirado | 0.94 |
| `RFC_PERMISSION` | Permission denied no RFC | 0.90 |
| `ODATA_AUTH` | OData authentication failed | 0.93 |
| `RFC_SYSTEM_ERROR` | Error do SAP system | 0.91 |
| `IDOC_PARSE` | Error ao parse IDoc | 0.90 |
| `RFC_QUEUE_FULL` | Queue RFC cheia (SMQ1/SMQ2) | 0.88 |
| `RFC_CONCURRENT` | RFC concorrente overload | 0.87 |
| `ODATA_CURRENCY` | Currency mismatch OData | 0.89 |
| `RFC_DISTRIBUTION` | Error no RFC distribution | 0.92 |
| `IDOC_STATUS` | IDoc status inválido | 0.91 |

## DA-53: Prompt Versioning

```python
# app/agent/prompts.py
PROMPT_VERSION = "v2.1.0"
PROMPT_DIGEST = "sha256:abc123..."  # Hash do template

DIAGNOSIS_PROMPT = PromptTemplate.from_template(
    """Você é um especialista em diagnóstico de incidentes de integração SAP.
Use o contexto abaixo para identificar a causa raiz do incidente descrito.
Se o contexto não for suficiente, diga explicitamente.

Contexto:
{context}

Descrição do incidente:
{description}

Responda com JSON:
{{
  "probable_root_cause": "...",
  "recommended_actions": ["...", "..."],
  "confidence": 0.0–1.0
}}
"""
)
```

**Resposta inclui:**
- `prompt_version = "v2.1.0"`
- `prompt_digest = "sha256:abc123..."`

## DA-20/26: Hybrid Inference

```python
# app/llm/factory.py
def get_chat_model() -> BaseChatModel:
    if settings.llm_provider == "ollama":
        return ChatOllama(model=settings.llm_model, temperature=0.0, seed=42)
    elif settings.llm_provider == "openai":
        return ChatOpenAI(model=settings.openai_model, temperature=0.0)
    else:
        raise ConfigurationError(f"Provider desconhecido: {settings.llm_provider}")

# app/llm/gateway.py
async def invoke_via_gateway(coro, policy: AIGatewayPolicy):
    """
    Policy enforcement + circuit breaker + budget check (DA-26/41/43).
    """
    # Fallback Ollama → cloud
    if policy == "strict":
        try:
            return await coro()
        except Exception as e:
            if settings.llm_provider == "ollama":
                # Fallback para cloud
                return await cloud_fallback()
            else:
                raise e
    elif policy == "cloud_with_dlp":
        # Redação de PII + cloud inference
        return await cloud_with_dlp(coro)
```

## DA-1/25: RAG Top-1 + Threshold

```python
# app/rag/retriever.py
def _evidence_admission_score(top_score: float) -> bool:
    """
    Evidence admission threshold (DA-25) → 0.3.
    """
    return top_score >= 0.3

# Na retrieve_node():
if not _evidence_admission_score(state["top_score"]):
    state["retrieved_context"] = None  # Fallback para "Nenhum contexto"
```

**Resultado:** `top_score = 0.85 >= 0.3` → contexto aceito

## DA-15/3: Guardrails

```python
# app/agent/nodes.py
def _apply_confidence_guardrails(state: CopilotState, result: dict) -> dict:
    """
    Evidence strength calculado deterministicamente (DA-15) + teto de 0.4 se fallback (DA-3).
    """
    connector_result = state.get("connector_result")

    if connector_result and connector_result.is_fallback:
        result["evidence_strength"] = min(result["evidence_strength"], 0.4)
        result["model_confidence"] = min(result["model_confidence"], 0.4)

    evidence_strength = state["top_score"] if state.get("top_score") else 0.0
    result["evidence_strength"] = evidence_strength

    return result
```

**Resultado:** `evidence_strength = 0.85`, `model_confidence = 0.92`

## DA-21: GraphRAG Error Handling

```python
# app/agent/nodes.py::graph_write_node()
try:
    await neo4j_client.execute(...)
except (DriverError, TransientError):
    pass  # DA-21: tolerância a falha Neo4j
```

## DA-30: PII Redaction + Backoff

```python
# app/redaction.py
def redact_pii(text: str) -> str:
    """
    PII redaction ampliado (DA-30): emails, telefones, CPFs, CNPJs, números de cartão.
    """
    patterns = [
        (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL]"),
        (r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b", "[CPF]"),
        (r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b", "[CNPJ]"),
        (r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b", "[CARD]"),
        # ... (mais padrões)
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text

# Backoff exponencial (invocation retry)
async def invoke_with_backoff(coro, max_retries=3, base_delay=1.0):
    """
    Backoff exponencial (DA-30).
    """
    for attempt in range(max_retries):
        try:
            return await coro()
        except Exception as e:
            if attempt == max_retries - 1:
                raise e
            delay = base_delay * (2 ** attempt)
            await asyncio.sleep(delay)
```

## DA-51: Quality Gates

| Gate | Status | Nota |
|---|---|---|
| `connector_validation_matrix` | ✅ | 10/10 conectores documentados |
| `connector_coverage` | ✅ | 27 linhas × 9 mecanismos (informativo) |
| `reranker_invariant` | ✅ | Sempre `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` |
| `prompt_digest_measured` | ✅ | Digest de prompt alinhado |
| `candidate_das_fresh` | ✅ | DAs candidatas não têm seção implemented |

---

**Status UC-1:** ✅ completo (HTTP → response + da-33/53/20/21/25/30)
