# Aula 22:Testing & Validation

**Status:** *Draft iniciado*
**Última atualização:** 2026-10-03
**Nível:** Avançado
**Duração estimada:** 3–4 horas

---

## Onde Vive

Este documento está em `docs/DA_AULA_22_TESTING.md`.

---

## Objetivo

Guia prático para **testar o Integration Incident Copilot** em todos os níveis:

1. **Unitários** (funções/módulos isolados, sem infra)
2. **Integração** (com Qdrant/Ollama/Postgres)
3. **E2E** (fluxo completo HTTP → response)
4. **Quality Gates** (validação automática de documentação, dataset, DAs)

Ao final, você será capaz de:

- Executar testes unitários (`pytest -m "not integration"`)
- Executar testes de integração (`pytest -m integration`)
- Verificar quality gates (`uv run python scripts/quality_gate.py`)
- Debugar falhas em cada nível

---

## Pré-requisitos

### 1. Ambiente de Teste

```bash
# Virtualenv ativo
PATH="$PWD/.venv/bin:$PATH"

# Variáveis de ambiente (opcional)
# IIC_TEST_DATABASE_URL=postgresql://…  # para e2e com Postgres
```

### 2. Infraestrutura (para testes de integração)

```bash
# Qdrant + Postgres
docker compose --profile observability up -d postgres qdrant

# Ollama (host nativo, /usr/local/bin/ollama)
# Se usar container Ollama: docker compose --profile container-ollama up -d ollama
```

### 3. Conhecimento Prévio

- `pytest` + `pytest-asyncio` (modo strict)
- `httpx.AsyncClient` para testes de API
- `pytest.mark.integration` vs `pytest.mark.unit`
- Quality Gates determinísticos (`app/evaluation/gates.py`)

---

## Estrutura de Testes

### 1. Localização

```
tests/
├── unit/               # Testes sem infra
│   ├── test_agent/     # Nodes, rules, supervisor
│   ├── test_connectors/ # 10 conectores
│   ├── test_llm/       # Factory, gateway, fallback
│   ├── test_rag/       # Retriever, ingest
│   └── test_admin/     # Models, crypto, metering
├── integration/        # Com infra (Qdrant/Ollama)
│   ├── test_agent/
│   ├── test_connectors/
│   └── test_e2e/       # Flask/FastAPI client
└── conftest.py         # Fixtures (httpx client, loop, tmpdir)
```

### 2. Marcadores (`-m`)

| marcador | descrição | infra exigida |
|---|---|---|
| (nada) | todos os testes unitários | nenhuma |
| `integration` | testes com infra (Qdrant/Ollama) | sim |
| `slow` | testes longos (>1s) | opcional |

### 3. Exemplos

#### Unitário (rápido)

```bash
# Todos os unitários
uv run pytest tests/ -m "not integration" -v

# Só agent
uv run pytest tests/unit/test_agent/ -v

# Só connectors
uv run pytest tests/unit/test_connectors/ -v
```

#### Integração (lento, exige infra)

```bash
# Tudo com infra
uv run pytest tests/ -v

# Só e2e
uv run pytest tests/integration/test_e2e/ -v
```

---

## Quality Gates

### 1. Lista de Gates (da DA-51)

| gate | descrição |
|---|---|
| `prompt_digest_measured` | digest de prompt coherence com baseline |
| `connector_reachable` | conector na registry + Literals + supervisor + CLI + UI + seed web_search_sources |
| `connector_coverage` | conector em `docs/CONNECTORS.md` (mapa SAP × mecanismo) |
| `candidate_das_fresh` | DAs candidatas não têm seção em `docs/ARCHITECTURE.md` |
| `implemented_das_documented` | DAs implementadas têm prosa em `docs/ARCHITECTURE.md` |
| `das_index_current` | índice de DAs no `README.md` está atualizado |
| `da_registered` | DA citada em código tem linha na tabela |
| `docs_markup_integrity` | todos os links `.md` apontam para arquivos existentes |
| `llm_registry_sync` | registro de LLMs sincronizado com `app/llm/routes.py` |
| `web_search_sources_sync` | `web_search_sources` sincronizado com `app/agent/nodes.py` |
| `system_contracts_baselines` | baseline de contracts existe |
| `graph_rag_enabled` | config `USE_GRAPH_RAG` sincronizada com `rag/graph_store.py` |
| `modalities_sync` | `llm/modalities.py` sincronizado com `app/admin/models.py` |

### 2. Execução

```bash
# Executar todos os gates
uv run python scripts/quality_gate.py

# Executar gates específicos
uv run python scripts/quality_gate.py --gate connector_reachable
uv run python scripts/quality_gate.py --gate prompt_digest_measured
```

### 3. Saída

```
[QGATE] Running prompt_digest_measured...
[QGATE] prompt_digest_measured: PASS (digest coherence check)

[QGATE] Running connector_reachable...
[QGATE] connector_reachable: PASS (8 superfícies verificadas)

[QGATE] Running docs_markup_integrity...
[QGATE] docs_markup_integrity: PASS (17/17 links válidos)

All gates passed: 17/17
```

---

## Testes de Connector

### 1. Padrão de Teste

```python
# tests/unit/test_connectors/test_odata.py
@pytest.mark.asyncio
async def test_odata_fetch_success(httpx_mock: httpx.MockTransport):
    httpx_mock.add_response(
        url="https://services.odata.org/V2/Northwind/Northwind.svc/Customers",
        json={"value": [{"CustomerID": "ALFKI"}]}
    )
    
    connector = ODataConnector(...)
    result = await connector.fetch("ALFKI")
    
    assert result.success
    assert result.payload["CustomerID"] == "ALFKI"
```

### 2. Mock vs. Real

| cenário | abordagem |
|---|---|
| **HTTP 2xx** | `httpx.MockTransport` + `add_response()` |
| **HTTP 4xx/5xx** | `add_response(status_code=500)` |
| **Timeout** | `add_exception(httpx.TimeoutException)` |
| **Infra real** | `@pytest.mark.integration` + `docker compose up` |

---

## Testes de Agent/Graph

### 1. Mockar Nodes

```python
@pytest.mark.asyncio
async def test_supervisor_sap_route():
    state = CopilotState(
        messages=[HumanMessage(content="IDoc stuck in SAP")],
        domain="sap",
        connector_type="odata",
        connector_source_system="sap-erp"
    )
    
    # Mock supervisor (não chama LLM)
    with patch("app.agent.supervisor.classify_domain") as mock_classify:
        mock_classify.return_value = "sap"
        
        result = await supervisor_node(state)
        
        assert result["domain"] == "sap"
        assert result["connector_type"] == "odata"
```

### 2. Mockar Retriever

```python
@pytest.mark.asyncio
async def test_retrieve_node_fallback():
    state = CopilotState(
        messages=[HumanMessage(content="problema integration SAP")],
        evidence=[],
        context_documents=[]
    )
    
    # Mock RetrievalResult
    mock_retrieval = RetrievalResult(
        chunks=[Chunk(text="IDocs são mensagens SAP...", score=0.3)],
        strategy="dense+sparse+reranker",
        metadata={"source": "reference_library"}
    )
    
    with patch("app.agent.nodes.vector_store_search") as mock_search:
        mock_search.return_value = mock_retrieval
        
        result = await retrieve_node(state)
        
        assert len(result["context_documents"]) == 1
        assert result["context_documents"][0].source == "reference_library"
```

---

## Testes de LLM/Fallback

### 1. Mockar Hybrid Fallback

```python
@pytest.mark.asyncio
async def test_llm_fallback_on_failure():
    # Primeira chamada (Ollama) falha
    with patch("app.llm.factory.invoke_with_hybrid_fallback") as mock_invoke:
        mock_invoke.side_effect = [LLMError(" Connection refused"), 
                                  MockResponse(content="resposta mockada")]
        
        result = await invoke_via_gateway(...)
        
        assert mock_invoke.call_count == 2
        assert result.content == "resposta mockada"
```

### 2. Mockar Gateway

```python
@pytest.mark.asyncio
async def test_gateway_budget_exceeded():
    with patch("app.llm.gateway.invoke_via_gateway") as mock_gateway:
        mock_gateway.side_effect = BudgetExceededError("daily budget reached")
        
        with pytest.raises(BudgetExceededError):
            await invoke_via_gateway(...)
```

---

## Testes de RAG

### 1. Mockar Qdrant

```python
@pytest.mark.asyncio
async def test_retriever_dense_sparse_rerank():
    mock_chunks = [
        Chunk(text="IDocs são mensagens SAP", score=0.9),
        Chunk(text="IDoc stuck causa delay", score=0.85),
        Chunk(text="IDoc review procedure", score=0.8)
    ]
    
    with patch("app.agent.retriever.vector_store_search") as mock_search:
        mock_search.return_value = RetrievalResult(
            chunks=mock_chunks,
            strategy="dense+sparse+rerank",
            metadata={}
        )
        
        result = await RAG.retrieve("IDoc stuck", top_k=3)
        
        assert len(result.chunks) == 3
        assert result.chunks[0].score == 0.9
```

### 2. Mockar Reranker

```python
def test_reranker_score():
    passages = [
        ("pergunta", "IDoc stuck"),
        ("pergunta", "SAP integration"),
        ("pergunta", "OData error")
    ]
    
    scores = [0.9, 0.7, 0.4]
    
    with patch("app.rag.retriever.reranker.predict") as mock_predict:
        mock_predict.return_value = [[score] for score in scores]
        
        result = rerank(passages)
        
        assert result[0][0] == "IDoc stuck"  # top score
```

---

## Debug de Testes

### 1. VS Code Launch Configuration

```jsonc
{
  "name": "Debug: pytest (unitários)",
  "type": "python",
  "request": "launch",
  "module": "pytest",
  "args": [
    "tests/unit/",
    "-m", "not integration",
    "-v"
  ],
  "env": {
    "IIC_TEST_DATABASE_URL": ""
  }
}
```

### 2. Flags Úteis

| flag | descrição |
|---|---|
| `-v` | verbose (detalha testes passando) |
| `-x` | stop at first failure |
| `--tb=short` | traceback resumido |
| `--maxfail=N` | para após N falhas |
| `-k "test_name"` | filtra por nome |

---

## CI/CD

### 1. Workflow `.github/workflows/test.yml`

```yaml
jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      
      - name: Install dependencies
        run: |
          pip install uv
          uv sync --dev
      
      - name: Unit tests (rápidos)
        run: uv run pytest tests/ -m "not integration" -v
      
      - name: Quality gates
        run: uv run python scripts/quality_gate.py
```

### 2. Testes no CI

- **Unitários:** sempre rodam (rápidos, sem infra)
- **Integração:** só rodam em branches de feature (infra disponível)
- **E2E:** só rodam com `IIC_TEST_DATABASE_URL` (Postgres provisionado)

---

## Pattern de Teste Recomendado

### 1. Arrange-Act-Assert

```python
def test_odata_fetch_not_found():
    # Arrange
    connector = ODataConnector(
        base_url="https://services.odata.org/V2/Northwind/Northwind.svc",
        auth_type="basic",
        username="user",
        password="pass"
    )
    
    httpx_mock.add_response(
        url=f"{connector.base_url}/Customers('NONEXISTENT')",
        status_code=404,
        json={"error": {"message": "Customer not found"}}
    )
    
    # Act
    result = await connector.fetch("NONEXISTENT")
    
    # Assert
    assert not result.success
    assert result.error_code == "NOT_FOUND"
    assert "Customer not found" in result.error_message
```

### 2. Parametrização

```python
@pytest.mark.parametrize("connector_type,expected_auth", [
    ("odata", "basic"),
    ("sap_cap", "jwt"),
    ("servicenow", "oauth2"),
])
def test_connector_authStrategy(connector_type, expected_auth):
    connector = get_connector(connector_type)
    assert connector.auth_strategy == expected_auth
```

---

## Checklist de Teste Completo

- [ ] **Unitários**
  - [ ] Todos os nodes do grafo
  - [ ] 10 conectores (mock HTTP)
  - [ ] LLM factory + fallback
  - [ ] RAG retriever + reranker
  - [ ] Admin crypto + metering
- [ ] **Integração**
  - [ ] Qdrant (retrieval)
  - [ ] Ollama (LLM invoke)
  - [ ] Postgres (persistência)
  - [ ] FastAPI client (`httpx.AsyncClient`)
- [ ] **E2E**
  - [ ] `/diagnose` (HTTP → response)
  - [ ] `/a2a` (Agent2Agent JSON-RPC)
  - [ ] `/admin/*` (UI + API)
  - [ ] `/events/webhook` (CloudEvents)
- [ ] **Quality Gates**
  - [ ] `prompt_digest_measured`
  - [ ] `connector_reachable`
  - [ ] `connector_coverage`
  - [ ] `docs_markup_integrity`
  - [ ] `candidate_das_fresh`
  - [ ] `implemented_das_documented`

---

## Referências

- DA-15: Evidence/Trust Layer determinística
- DA-25: Evidence/Trust Layer v2 + threshold RAG
- DA-30: PII redaction + backoff exponencial
- DA-32/40: AMQP 1.0 (Solace/Event Mesh)
- DA-45: Universal provider (origin real, routes by destination)
- DA-51: Quality gates (17 gates, validations)
- DA-53: Prompt versioning + digest baseline
- DA-57: Web search policy (fail-closed, no hardcoded fallback)

---

**Status:** *Draft iniciado — aguardando revisão e expansão*
