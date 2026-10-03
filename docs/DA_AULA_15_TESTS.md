# DA_AULA_15 — Testes e Avaliação

## Objetivo

Documentar a camada de testes e avaliação do Integration Incident Copilot, cobrindo:

- Estrutura da suíte de testes (54 unitários + 14 integração, sem subdiretórios)
- Categorização unit/integration via pytest markers
- Mocks: `pytest-mock/monkeypatch`, `unittest.mock/patch`, cassettes VCR em `tests/cassettes/`
- Fixtures compartilhadas (`db_session`, `mock_http`, `cassette_loader`)
- Gates de qualidade: thresholds RAG/hit@1/margin de reranker, validação de datasets (DA-29/DA-51)
- Padrões práticos: isolamento de testes (limpeza de variáveis globais, reset de singletons), fail-closed em CI

---

## Arquitetura da Suíte de Testes

### Estrutura

```
tests/
├── conftest.py                 # configuração compartilhada, skip logic, fixtures
├── cassette_loader.py          # helper para carregar respostas simuladas
├── cassettes/                  # 9 arquivos JSON com respostas HTTP reais
│   ├── servicenow_*.json
│   ├── odata_timeout_cpi.json  # exemplo: dados de demonstração do SAP CPI
│   ├── salesforce_*.json
│   └── ...
├── test_*.py                   # 68 arquivos de teste (54 unitários + 14 integração)
└── __init__.py
```

**Sem subdiretórios** — todos os testes vivem em `tests/` único.

### Categorização: unitário vs. integração

**Unitários** (54 arquivos): não exigem infra externa (Qdrant/Ollama), rodam automaticamente com `uv run pytest tests/ -m "not integration"`.

**Integração** (14 arquivos): exigem stack local (Qdrant + Ollama), marcadados com `@pytest.mark.integration`, pulados automaticamente se qualquer porta estiver fechada (portas 6333/6335 para Qdrant, 11434 para Ollama).

**Skip logic em `conftest.py`** (l.93-102):

```python
def pytest_collection_modifyitems(config, items):
    if _stack_available():  # Qdrant e Ollama both up
        return
    skip_marker = pytest.mark.skip(
        reason="Stack local (Qdrant/Ollama) indisponivel em 127.0.0.1..."
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_marker)
```

---

## Fixtures e Isolamento

### Constantes globais limpas

`conftest.py` (l.29-38) fixa variáveis críticas **antes do import do app** para evitar dependência de `.env` local:

```python
os.environ["API_KEY"] = ""
os.environ["A2A_API_KEY"] = ""
os.environ["ADMIN_API_KEY"] = ""
os.environ["DATABASE_URL"] = ""
```

**Por que?** `app/main.py` gera chaves aleatórias no startup (DA-18/DA-19/DA-23). Com uma chave real no `.env`, testes de autenticação falhariam (401) pois esperam sessão vazia.

### Reset de singletons entre testes

Dois fixtures globais, `autouse=True`, garantem que testes não interfiram uns nos outros:

#### Fixtures em `conftest.py` (l.65-112)

1. **Rate limiter reset** (l.65-77):
   - `slowapi.Limiter` (shared storage por processo) → sem reset, cota acumula entre testes
   - exemplo: 2° teste testando rate limit pode falhar com 429 por chamadas anteriores

2. **Connector circuit breaker reset** (l.80-90):
   - `connector_circuit_breaker` (singleton) → falhas de rede em um teste deixariam o circuito aberto para testes subsequentes

3. **Event idempotency** (l.105-112):
   - `idempotency._local_seen` (dict temporário) → limpo entre testes para evitar descarte de CloudEvents repetidos (ex.: mesmo `id` em outro teste seria rejeitado)

---

## Padrões de Mock

### 1. `pytest-mock/monkeypatch` (predominate)

Reescreve atributos globais temporariamente — ideal para configurar mocks sem alterar código.

**Exemplo: `/health` reflete configuração de `.env` (test_api.py, l.82-92):**

```python
def test_health_endpoint_reflects_connector_config(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.settings.odata_service_url", "https://sap.example.com/odata"
    )
    assert client.get("/health").json()["connectors"]["odata"]["status"] == "real"

    monkeypatch.setattr("app.connectors.settings.odata_service_url", "")
    assert client.get("/health").json()["connectors"]["odata"]["status"] == "mock"
```

**Por que não `patch`?** `monkeypatch` reverte automaticamente após o teste, evitando vazamento de estado.

### 2. `unittest.mock/patch` (casos específicos)

Usado quando a reescrita é mais complexa (ex.: métodos de instância).

**Exemplo: redefinir resposta de um provider fake (test_llm_gateway.py, l.201-215):**

```python
from unittest.mock import patch


def test_invoke_via_gateway_fallback_timeout():
    with patch("app.llm.gateway.invoke_with_hybrid_fallback") as mock_invoke:
        mock_invoke.side_effect = httpx.TimeoutException("timeout simulado")
        with pytest.raises(httpx.TimeoutException):
            invoke_via_gateway("prompt", state)
```

### 3. Cassette VCR (simular respostas reais)

**Arquivo: `tests/cassette_loader.py`** (unico helper, não dentro de `conftest.py` para poder ser importado diretamente):

```python
@cache
def load_cassette(name: str) -> dict[str, Any]:
    """Le tests/cassettes/<name>.json e devolve so o campo "response"."""
    path = _CASSETTES_DIR / f"{name}.json"
    data = json.loads(path.read_text())
    return data["response"]  # _source metadado é ignorado
```

**Exemplo de uso (test_connectors.py):**

```python
from cassette_loader import load_cassette


def test_servicenow_incident_found(monkeypatch):
    cassette = load_cassette("servicenow_incident")
    monkeypatch.setattr(
        "app.connectors.servicenow_connector.ServiceNowConnector._fetch",
        lambda self, identifier: cassette,
    )
    result = connector.fetch("SIR-123")
    assert result.status == "success"
    assert result.source_system == "ServiceNow"
```

**Arquivos em `tests/cassettes/`:**
`servicenow_incident.json`, `ariba_purchase_order.json`, `po_message_monitor.json`, `cpi_message_status.json`, etc.
Cada arquivo é uma **reposta HTTP real** gravada em formato controlado (`_source` documenta origem), não texto arbitrário.

---

## Gates de Qualidade (DA-29/DA-51/DA-58)

### `app/evaluation/gates.py`

Define thresholds e validadores determinísticos (não dependem de LLM/Qdrant/Ollama).

**Contrato padrão:** cada gate retorna `list[Finding]`, onde `Finding` tem `severity ∈ {"pass", "fail", "warn"}`.

#### Thresholds padrão (l.38-46):

```python
@dataclass(frozen=True)
class Thresholds:
    min_rag_cases: int = 15  # dataset RAG mínimo
    min_hit_at_1: float = 0.90  # hit@1 para RAG (DA-29)
    min_benchmark_margin: float = 0.05  # margem entre reranker atual vs baseline
    min_promptfoo_cases: int = 1  # mínimo de casos promptfoo
```

#### Gates principais (DA-51):

| Gate | Check | Threshold |
|------|-------|-----------|
| `rag_dataset_schema` | Estrutura e campos obrigatórios | mínimo 15 casos |
| `reranker_invariant` | Reranker é `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (DA-29) | invariante |
| `corpus_coverage` | Fontes esperadas existem em `data/sample_docs/*.md` | 100% cobertura |
| `connector_reachable` | Conector registrado em 8+ superfícies (registry, Literals, supervisor, CLI, UI, seed `web_search_sources`, `connector_coverage.yaml`, `docs/ARCHITECTURE.md`) | 100% cobertura |
| `connector_coverage` | Mapa produto SAP × mecanismo (`dedicated`/`generic`/`absent`) sem inconsistências | `unknown` ≠ `none` |
| `prompt_digest_measured` | Digest de `app/agent/prompts.py` coincide com `data/eval/prompt_baseline.json` | SHA-256 idêntico |
| `candidate_das_fresh` | Nenhuma DA marcada como candidata tem seção em `docs/ARCHITECTURE.md` | sem inconsistências |
| `da_registered` | DA citada no código tem linha na tabela de `CLAUDE.md` | sem órfãs |
| `docs_markup_integrity` | Delimitadores de código (` ```python `) consistem | sem breaks |
| `docs_code_references` | `app/x.py::símbolo` mencionado na doc exite | sem links quebrados |

#### Pipeline completo

**Arquivo: `scripts/quality_gate.py`**

CLI: `uv run python scripts/quality_gate.py [--strict] [--write-prompt-baseline]`

- **Modo `--strict`**: fail se qualquer `severity == "fail"`
- **Modo `--write-prompt-baseline`**: recalcula digest de prompt e grava em `data/eval/prompt_baseline.json`

---

## Exercícios Práticos

### 1. Identificar teste unitário vs. integração

**Exemplo 1 (unitário):**

```python
def test_real_connector_data_is_confidential():
    assert classify_sensitivity({"connector_data": _real_connector_data()}) == "confidential"
```

- Não tem `@pytest.mark.integration` → roda automaticamente
- Usa apenas dados Python (`classify_sensitivity` é função pura) → não toca Qdrant/Ollama

**Exemplo 2 (integração):**

```python
@pytest.mark.integration
def test_retriever_finds_correct_document():
    hits = retrieve(query, target="incidents", top_k=3)
    assert hits[0]["source"] == expected_source
```

- Tem `@pytest.mark.integration` → pulado sem stack local
- Chama `retrieve()` → consulta Qdrant (infra externa)

**Exercício:** Localize 5 exemplos de cada categoria em `tests/`.

---

### 2. Mapear infra necessária para testes de integração

**Tarefa:** Para cada arquivo em `tests/test_*_integração*.py`, indicar:

- Portas abertas necessárias (Qdrant? Ollama? Postgres? Neo4j?)
- Comando Docker Compose para subir infra
- Se o teste roda com `--profile` específico

**Resposta esperada (exemplos):**

- `tests/test_retriever.py`: Qdrant (6333/6335) + Ollama (11434) → `docker compose up -d`
- `tests/test_graph_store_neo4j_smoke.py`: Neo4j (7474) → `docker compose --profile graphrag up -d neo4j`
- `tests/test_amqp_consumer.py`: RabbitMQ (5672) → `docker compose --profile container-ollama up -d rabbitmq`

---

### 3. Analisar limitação de mock vs. integração real

**Exemplo:** `tests/test_connectors.py` (mock ServiceNow + cassette):

```python
def test_servicenow_incident_found(monkeypatch):
    cassette = load_cassette("servicenow_incident")
    monkeypatch.setattr(
        "app.connectors.servicenow_connector.ServiceNowConnector._fetch",
        lambda self, identifier: cassette,
    )
    result = connector.fetch("SIR-123")
    assert result.status == "success"
```

**O que prova:**
- Conector lida corretamente com resposta simulada
- Mapeamento de campos (status, source_system) é funcional

**O que NÃO prova:**
- Compatibilidade com tenant ServiceNow real (API não testada contra endpoint vivo)
- Timeout, rate limit, ou erros de rede reais (cassette é resposta estática)

**Conclusão:** mock serve para **funcionalidade**, integração real serve para **compatibilidade**.

---

## Invariantes Críticas (não alterar sem DA)

1. **`RERANKER_MODEL` invariante** (DA-29):
   Sempre `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` — gate `reranker_invariant` reprova se diferente.

2. **RAG `hit@1 ≥ 0.90`** (DA-29/DA-51):
   Margem mínima de `0.05` entre reranker atual e baseline. Threshold definido em `Thresholds.min_hit_at_1`.

3. **`rule_engine_enabled = True`** (DA-33):
   Nunca desligar Rule Engine em produção. Pode ser desligado SÓ em testes que forçam LLM.

4. **`evidence_strength` é FLOAT** (migration 002):
   Queries de dashboard NUNCA usam predicado textual (`IN ('high','critical')`). Usa `evidence_strength >= 0.62`.

5. **`prompt_digest`/`prompt_version` NULL quando rule engine encerra** (DA-53):
   Um diagnóstico sem LLM não foi produzido por prompt nenhum. Default `"desconhecido"` fabricaria procedência.

6. **Fail-closed em testes**:
   - `DataSensitivity == "confidential"` + provider cloud → `PolicyViolationError` (não warning)
   - `data_sensitivity` não configurada → bloqueia (não permite por omissão)

---

## Limitações Conhecidas

1. **Testes de integração requerem stack local**:
   Qdrant (6333/6335) + Ollama (11434) devem estar acessíveis. O CI CI pula automaticamente se qualquer porta fechada (DA-62).

2. **Cassettes são estáticos**:
   Respostas HTTP simuladas não renovam tokens nem simulam timeout/rate limit dinâmicos.

3. **Sem testes end-to-end de deploy**:
   Manifestos Kyma (`deploy/kyma/`) são validados estática (lint + yaml), mas não há deploy real em cluster (só manifests).

4. **Fallback em memória para circuit breaker**:
   `app/circuit_breaker.py` usa Redis quando disponível, fallback em-memória (não compartilhado entre replicas).

---

## Referências Rápidas

### Comandos

| Comando | O que faz |
|---------|-----------|
| `uv run pytest tests/ -m "not integration" -v` | Roda 54 unitários (sem infra) |
| `uv run pytest tests/ -v` | Roda todos (unitários + integração) — falha sem Qdrant/Ollama |
| `uv run python scripts/quality_gate.py --strict` | Valida daatasets/metrics/gates (CI) |
| `uv run python scripts/quality_gate.py --write-prompt-baseline` | Recalcula digest de prompt e grava baseline |

### Arquivos-chave

| Arquivo | Propósito |
|---------|-----------|
| `tests/conftest.py` | Configuração compartilhada, fixtures globais, skip logic |
| `tests/cassette_loader.py` | Helper para carregar cassettes de conectores |
| `app/evaluation/gates.py` | Thresholds e validadores determinísticos (DA-29/DA-51/DA-58) |
| `scripts/quality_gate.py` | Pipeline completo (CLI + ANSI report) |
| `data/eval/rag_eval_dataset.json` | Dataset de 22 casos RAG (easy/medium/hard/out_of_scope) |
| `data/eval/promptfoo_baseline.json` | Baseline de benchmark promptfoo |
| `docs/QUALITY_GATES.md` | Documentação das gates (o que cobre / o que não cobre) |

---

## DAs

### DA-29: Reranker benchmark e invariante

**Problema:** Reranker padrão (`ms-marco-L6`) tinha **-7pp Hit@1** vs alternativa (`mmarco-mMiniLMv2-L12-H384-v1`), e não havia gate para impedir regressão.

**Solução:**
1. Benchmark completo (`scripts/benchmark_rerankers.py`) com métricas Hit@1/MRR;
2. Invariante `RERANKER_MODEL` fortemente documentada (não pode mudar sem DA);
3. Gate `reranker_invariant` reprova se código usa modelo diferente do benchmark vencedor.

**Limitações:** Benchmark roda só localmente (precisa Ollama), não faz parte do CI.

---

### DA-51: Quality Gates integrados

**Problema:** Gates de qualidade estavam dispersos (some checks em CI, outros não), não havia baseline reprodutível, e documentação não era validada automaticamente.

**Solução:**
1. `app/evaluation/gates.py`统一.define thresholds (`Thresholds`) e validadores (`check_*`);
2. `scripts/quality_gate.py` executa todos os checks em sequência, com fallback gracefully e ANSI report;
3. Dataset (`data/eval/rag_eval_dataset.json`) + prompt baseline (`data/eval/prompt_baseline.json`) versionados.

**Limitações:** Qualidade de texto (coerência, fidelidade ao prompt) continua dependente de avaliação humana (promptfoo não valida isso).

---

### DA-58: Cobertura de conector calculada

**Problema:** Matriz produto SAP × mecanismo era mantida manualmente, com risco de inconsistência entre `connector_coverage.yaml` e documentação (ex.: docstring de `apim_connector.py` diz "Integration Suite", mas código lê *analytics*, não orquestração).

**Solução:**
1. Fontes versionadas (`data/sap_products.yaml`, `data/connector_coverage.yaml`);
2. Mapa calculado (`app/evaluation/coverage.py`, 3 níveis: `dedicated`/`generic`/`absent`);
3. Gate `connector_coverage` reprova só por **incoerência** (ex.: conector sem linha, produto fantasma), não por lacunas (lacunas são oportunidades de melhoria, não falhas).

**Limitações:** Nível `generic` é **especulativo** (não validado contra produto real — "cliente OData alcançaria" não é "temos conector de S/4HANA").
