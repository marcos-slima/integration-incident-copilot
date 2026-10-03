# DA-AULA 10: Nodes, Rule Engine, Escalation e Evidence Assembly

## Objetivo da Aula

Compreender os nodes do grafo LangGraph do Integration Incident Copilot, o Rule Engine determinístico (DA-33), o sistema de escalonamento baseado em sinais observáveis (DA-44) e a camada de Evidence/Trust (DA-16/25). Ao final, você será capaz de:
- Identificar e explicar cada um dos 8+ nodes do pipeline
- Explicar como o Rule Engine evita LLM chamadas desnecessárias
- Derivar decisões de escalonamento usando apenas fatos observáveis
- Montar um e-bundle de evidência com níveis de confiança distintos

---

## 1. O Grafo LangGraph: Nodes e Seus Papéis

### 1.1. Arquitetura do Pipeline

O pipeline LangGraph do Incident Copilot segue esta estrutura:

```
┌─────────────┐    ┌──────────┐    ┌──────────┐    ┌───────────┐
│supervisor   │──▶ │connector │──▶ │retrieve  │──▶ │graph_enrich│
└─────────────┘    └──────────┘    └──────────┘    └───────────┘
     │                    │             │                │
     │                    │             │                │
     ▼                    │             │                │
┌─────────────┐           │             │                │
│graph_write  │◀──────────┘             │                │
└─────────────┘                         │                │
     │                                  │                │
     ▼                                  ▼                ▼
┌─────────────────┐          ┌─────────────────┐  ┌───────────┐
│sap|saas|generic_│◀─────────┤web_search_node │  │graph_write│
│diagnosis_node   │          └─────────────────┘  └───────────┘
└─────────────────┘
         │
         ▼
   ┌─────────┐
   │report   │
   └─────────┘
```

### 1.2. Os 8+ Nodes do Pipeline

Todos os nodes são funções puras que recebem um `CopilotState` (TypedDict) e retornam um dict com novos campos ou updates ao state existente. Cada node é monitorado por Langfuse com `@observe_span`.

#### Node: `supervisor_node` (DA-22)

**Responsabilidade:** Classificar o domínio do incidente em `sap`, `saas` ou `generic`.

**Implementação:** `app/agent/supervisor.py::classify_domain()`

- Recebe: `interface_type` do request
- Usa mapas determinísticos: `_SAP_INTERFACE_TYPES`, `_SAAS_INTERFACE_TYPES`
- Retorna: `agent_domain` no state

**Exemplo:**
```python
interface_type = "odata"  # → agent_domain = "sap"
interface_type = "salesforce"  # → agent_domain = "saas"
interface_type = "generic-webhook"  # → agent_domain = "generic"
```

**Invariante DA-22:** _"classificação é 100% determinística, sem LLM"_

#### Node: `connector_node`

**Responsabilidade:** Consultar o sistema de origem (OData, RFC, ServiceNow, etc.)

**Implementação:** `app/agent/nodes.py::_invoke_connector()`

- Recebe: `connector_source_system`, `connector_identifier`
- Usa: `CONNECTOR_REGISTRY` (app/connectors/__init__.py)
- Retorna: `connector_data`, `interface_type`

**Campos de `connector_data`:**
- `status`: código HTTP/error code
- `error_code`: string da empresa de origem
- `message`: descrição do erro ou sucesso
- `raw`: payload bruto
- `is_mock`: booleano (fake/test data)
- `is_fallback`: booleano (identificador não reconhecido)
- `source_system`: nome do conector usado

**Exceção:** Se o conector não for encontrado no registry → exc. com indicação de quais tipos estão disponíveis.

#### Node: `retrieve_node` (DA-1/2/17/25)

**Responsabilidade:** Buscar contexto relevante no RAG (Qdrant + reranker).

**Implementação:** `app/agent/nodes.py::_run_retriever()`

**Fluxo:**
1. _Hybrid search_: dense (embeddings) + sparse (BM25)
2. _Rerank_: `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (DA-29)
3. _Threshold_: `REFERENCE_FALLBACK_THRESHOLD` (DA-25/42)
4. _Fallback_: se score < threshold → `reference_library` (DA-17)
5. _Top-1_: apenas o chunk mais relevante (`DA-1`)

**Retorna:** `retrieved_context` (lista com 0 ou 1 item)

**Item de `retrieved_context`:**
```python
{
    "source": "odata_timeout_cpi.md",
    "text": "Timeout ao chamar... [chunk completo]",
    "score": 0.82,  # dense+sparse hybrid
    "rerank_score": 0.71,  # cross-encoder
    "rerank_score_calibrated": 0.68,  # DA-42: sigmoid
    "collection": "sap_incident_docs",
}
```

#### Node: `graph_enrich_node` (DA-28)

**Responsabilidade:** Buscar contexto histórico do GraphRAG (Neo4j, opt-in).

**Implementação:** `app/agent/nodes.py::_run_graph_enrichment()`

- Usa: `GraphStore` (app/rag/graph_store.py)
- Busca: incidents passados com root cause semelhante
- Retorna: `graph_history` (lista de `RelatedIncident`)

**Invariante DA-21:** Trata `Neo4jError`, `DriverError`, `TransientError`

#### Node: `web_search_node` (DA-57)

**Responsabilidade:** Buscar na web (DuckDuckGo) quando o RAG falhar.

**Implementação:** `app/agent/nodes.py::web_search_node()`

**Regulado por DA-57:**
- `WEB_SEARCH_POLICY`: `disabled` (padrão), `approved`
- `resolve_approved_source(interface_type)`: devolve `site_filter` ou `None`

**Tool de busca web:**
```python
@lc_tool
def web_search_tool(query: str) -> str:
    """Busca SAP Community..."""
    # Politica de egress (DA-57):
    # - redact_pii_text (e-mail/CPF/CNPJ)
    # - remove URLs, GUIDs, IDs longos, tokens
    # - truncar a 200 chars
    safe_query = _sanitize_web_search_query(query)
```

**Retorna:** `web_search_results` (lista com 0 ou 1 item)

#### Node: `sap_diagnosis_node` / `saas_diagnosis_node` / `generic_diagnosis_node` (DA-22/33/53/59)

**Responsabilidade:** Executar o agente ReAct com persona específica.

**Implementação:** `app/agent/nodes.py::_run_diagnosis_agent()`

**Fluxo interno:**
1. **DA-33:** Rule Engine determinístico (antes de LLM!)
2. **Prompt building:** `_build_diagnosis_prompt(state, persona)`
3. **Sanitização:** remove padrões de prompt injection
4. **Agente ReAct v1.3:** chama LLM com tools (web_search)
5. **Structured output:** `DiagnosisModel` (Pydantic)
6. **Guardrails:** `_apply_confidence_guardrails()`

**Persona por domínio (DA-22/59):**
- `sap`: "Especialista SAP S/4HANA, CPI, Ariba..."
- `saas`: "Especialista em integração entre SaaS..."
- `generic`: "Especialista geral em integração..."

**Retorna:** `diagnosis` (dict com schema `DiagnosisModel`)

#### Node: `report_node` (DA-15/16/25)

**Responsabilidade:** Montar o relatório humano legível.

**Implementação:** `app/agent/nodes.py::report_node()`

**Campos do report (DA-25/26):**
- `probable_root_cause`: string
- `model_confidence`: float (0-1, auto-relatado + guardrails)
- `diagnosis_confidence`: float (0-1, calculado pelo pipeline)
- `evidence_strength`: float (0-1, fatos observáveis)
- `matched_source`: string ou null
- `next_steps`: list[str]
- `llm_provider_used`: string
- `prompt_version`: string (DA-53)
- `prompt_digest`: string (SHA-256, DA-53)

**Evidence Bundle (DA-16/25):**
```python
evidence_items = _assemble_evidence(state)
primary = [e for e in evidence_items if e["trust_level"] == "system_observed"]
supporting = [e for e in evidence_items if e["trust_level"] != "system_observed"]
```

**Output final (MD):**
```markdown
## Diagnóstico do Incidente

**Causa raiz provável:** O timeout ocorreu porque...

**Confiança:**
- `diagnosis_confidence` (pipeline): 85%
- `model_confidence` (LLM pos-guardrail): 78%
- `evidence_strength` (retrieval/conector): 68%

**Primary Evidence** (alta confiança):
- [system_observed] connector:odata_sap

**Supporting Facts** (revisão humana recomendada):
- [retrieved_document] rfc_timeout.md (rerank=0.68)
```

---

## 2. Rule Engine (DA-33)

### 2.1. Problema e Solução

**Problema:** 48% dos incidentes têm padrões conhecidos (DA-33, Fase 12), chamar LLM para eles é:
- **Custo:** tokens desnecessários
- **Latência:** espera pelo modelo
- **Consistência:** mesmo erro pode ser explicado de formas diferentes

**Solução:** Camada zero de inferência — regras regex pré-definidas avaliadas **antes** de chamar LLM.

### 2.2. Implementação

**Arquivo:** `app/agent/rules.py`

**Regras (21 patterns atuais):**
```python
_RULES = [
    {
        "id": "sap_idoc_status_51",
        "patterns": [r"IDoc.*status.*51", r"IDOC_STATUS_51"],
        "explanation": "IDoc travado em status 51: checar transaçãoWE02/WE05",
    },
    {
        "id": "odata_timeout",
        "patterns": [r"timeout.*odata", r"HTTP 504.*odata"],
        "explanation": "Timeout ao acessar OData: checar ICF services e firewall",
    },
    # ... mais 19
]
```

**Função principal:**
```python
def match_known_error(text: str, has_connector_data: bool = True) -> dict | None:
    """Se alguma regra casar, devolve diagnosis dict com confidence=0.90.
    NUNCA chama LLM."""
    for rule in _RULES:
        for pattern in rule["patterns"]:
            if re.search(pattern, text, re.IGNORECASE):
                return {
                    "probable_root_cause": rule["explanation"],
                    "matched_source": f"rule_engine:{rule['id']}",
                    "confidence": 0.90,
                    "llm_provider_used": "rule_engine",
                    "prompt_version": None,  # NÃO tem prompt!
                    "prompt_digest": None,  # NÃO tem digest!
                }
    return None  # Nenhuma regra casou → LLM necessário
```

### 2.3. Ordem de Avaliação

1. **Rule Engine** (determinístico, before LLM)
2. **LLM** (Se rule engine não casar)

**Invariante DA-33:** _"Rule Engine determinístico — camada zero de custo, avaliada ANTES de LLM"_

### 2.4. Limitações Conhecidas (DA-33)

- Replaces a simple keyword search with a full regex engine (no support for fuzzy matching yet)
- Patterns are static — não aprende com novos incidentes (futuro: DA-33 v2 com ML)
- Sem suporte a regras com contexto多-passos (ex: "se erro X, verificar Y, se falhar, fazer Z")

---

## 3. Escalation (DA-44)

### 3.1. Problema e Solução

**Problema:** Como decidir quando o tier 2 (modelo local) não é bom o suficiente para tier 3 (modelo pago: GPT/Claude/Gemini)?

**Fórmula errada:** `evidence_strength` (DA-15) — tem **3 flaws** (DA-44):
1. **Piso que satura:** `max(rag_score, 0.75)` com conector real → nunca abaixo de 0.75
2. **Ignora origin:** mesmo score tem peso diferente em `sap_incident_docs` vs `sap_reference_library`
3. **Satura e não discrimina:** mede "quantidade de contexto", não "acertou ou errou"

**Solução:** Sinal determinístico **aditivo**, avaliando apenas fatos observáveis:

### 3.2. Implementação

**Arquivo:** `app/agent/escalation.py`

**Sinais observáveis:**
```python
-data.is_mock / data.is_fallback  # conector real?
-hit["collection"]  # curated (sap_incident_docs) ou floor (sap_reference_library)?
-hit["rerank_score_calibrated"]  # DA-42: score pós-reranker, calibrado por sigmoid
-diagnosis["matched_source"] is None  # guardrail anulou fonte? (DA-16)
```

**Decisão:** `compute_escalation_signal(state) -> EscalationDecision`

**Regras (ordem de prioridade):**
1. **Regra 0:** Sem contexto → `NO_CONTEXT` → `should_escalate=False`
2. **Regra 1:** Abstenção (`matched_source=None`) → `ABSTAINED` → `should_escalate=True`
3. **Regra 2:** `collection=FLOOR_COLLECTION` e `top_evidence < 0.62` → `FLOOR_TIER_WEAK` → `should_escalate=True`
4. **Regra 3:** `collection=CURATED_COLLECTION` e `top_evidence < 0.45` → `CURATED_TIER_WEAK` → `should_escalate=True`
5. **Regra 4:** Else → `GROUNDED` → `should_escalate=False`

**Exemplos reais (DA-44):**
```python
# Caso 1: IDoc status 51 (rule engine resolveu)
diagnosis = {
    "matched_source": "rule_engine:sap_idoc_status_51",
    ...
}
# Resultado: top_evidence=1.0, reason=GROUNDED, should_escalate=False ✓

# Caso 2: OData timeout, matched_source alucinado (guardrail anulou)
diagnosis = {
    "matched_source": None,  # Anulado por _apply_confidence_guardrails()
    ...
}
# Resultado: reason=ABSTAINED, should_escalate=True ✓

# Caso 3: Reference library com score baixo
hit = {
    "collection": "sap_reference_library",
    "rerank_score_calibrated": 0.38
}
# Resultado: reason=FLOOR_TIER_WEAK, should_escalate=True ✓
```

**Escalonamento real:** Quem invoca o tier 3 é o **AI Gateway** (DA-26), que aplica `data_sovereignty_mode` (DA-43).

---

## 4. Evidence Assembly (DA-16/25)

### 4.1. Princípio Central

**Invariante DA-16:** _"_`_assemble_evidence()` **NUNCA usa autoavaliação do LLM** — somente fontes observáveis deterministicamente._

O LLM pode dizer "usei X e Y", mas isso não é confiável. O pipeline deve apontar **quais fontes ele de fato consultou**.

### 4.2. Implementação

**Arquivo:** `app/agent/nodes.py::_assemble_evidence()`

**Origens de evidência (em ordem de força):**

| trust_level | Fonte | Exemplo |
|-------------|-------|---------|
| `system_observed` | Conector real (não mock/fallback) | OData connector de verdade, RFC real |
| `system_observed` | Rule Engine | `rule_engine:sap_idoc_status_51` (DA-33) |
| `simulated` | Conector mock/fallback | RFC simulator, fallback por ID not found |
| `retrieved_document` | RAG (Qdrant) | `sap_incident_docs`, `sap_reference_library` |
| `retrieved_document` | GraphRAG | Historical incident from Neo4j |
| `web_untrusted` | Search web | DuckDuckGo results |
| `user_reported` | Description | Texto livre do incidente |

**Código:**
```python
def _assemble_evidence(state: CopilotState) -> list[dict]:
    evidence: list[dict] = []

    # DA-33: Rule Engine (max confiança)
    if str(diagnosis.get("llm_provider_used", "")).startswith("rule_engine"):
        evidence.append({
            "source_type": "rule_engine",
            "trust_level": "system_observed",
            ...
        })

    # Conector
    if data:
        trust = "simulated" if (data.is_mock or data.is_fallback) else "system_observed"
        evidence.append({
            "source_type": "connector",
            "trust_level": trust,
            ...
        })

    # RAG
    for hit in state.get("retrieved_context") or []:
        evidence.append({
            "source_type": "rag",
            "trust_level": "retrieved_document",
            "rerank_score": hit.get("rerank_score_calibrated"),  # DA-42
            ...
        })

    # Web_search
    if web_results and web_results[0].get("source") == "web_search":
        evidence.append({
            "source_type": "web",
            "trust_level": "web_untrusted",
            ...
        })

    # User description
    if description:
        evidence.append({
            "source_type": "user",
            "trust_level": "user_reported",
            ...
        })

    return evidence
```

### 4.3. Evidence Bundle no Report (DA-25/26)

**No `report_node`, evidence é classificada em dois blocos:**

```python
evidence_items = _assemble_evidence(state)
primary = [e for e in evidence_items if e["trust_level"] == "system_observed"]
supporting = [e for e in evidence_items if e["trust_level"] != "system_observed"]
```

**Output:**
- **Primary Evidence:** system_observed (alta confiança, não requer revisão humana)
- **Supporting Facts:** simulated, retrieved_document, web_untrusted, user_reported (revisão humana recomendada)

---

## 5. Exercícios Práticos

### 5.1. Identificação de Nodes

Dado o state abaixo, identifique quais nodes foram executados e em qual ordem:

```python
state = {
    "interface_type": "odata",
    "connector_source_system": "sap_s4hana",
    "connector_identifier": "MAT-12345",
    "description": "Timeout ao criar material",
    "logs": "...",
    "payload": "{...}",
}
```

**Solução:**
1. `supervisor_node` → `agent_domain = "sap"`
2. `connector_node` → `connector_data = {...}`
3. `retrieve_node` → `retrieved_context = [{"source": "odata_timeout_cpi.md", ...}]`
4. `sap_diagnosis_node` → `diagnosis = {...}`
5. `report_node` → `report_markdown = "..."`

**Pergunta extra:** Se `WEB_SEARCH_POLICY=approved`, qual node adicional pode rodar?

**Resposta:** `web_search_node` (se o agente ReAct decidir chamá-lo)

### 5.2. Rule Engine

Considere estas duas descrições de incidente:

**A)** "IDoc 456789012345678901 em status 51, checar WE02"
**B)** "Erro desconhecido ao processar pedido 12345"

Para cada uma, indique:
1. Rule engine casará?
2. Se sim, qual `rule_engine_category`?
3. `llm_provider_used` no diagnosis?

**Solução A:** Sim, `rule_engine_category = "sap_idoc_status_51"`, `llm_provider_used = "rule_engine"` (DA-33)
**Solução B:** Não, LLM necessário

### 5.3. Escalation Signal

Dado o state abaixo, calcule a decisão de escalonamento:

```python
state = {
    "connector_data": None,
    "retrieved_context": [
        {
            "collection": "sap_reference_library",
            "rerank_score_calibrated": 0.58,
            "rerank_score": 0.72,
            "score": 0.69,
        }
    ],
    "diagnosis": {"matched_source": "odata_timeout_cpi.md"},
}
```

**Passos:**
1. `has_context = True` (hit existe) → Regra 0 não casa
2. `abstained = False` (matched_source não é None) → Regra 1 não casa
3. `collection = "sap_reference_library" (FLOOR_COLLECTION)` e `top_evidence = 0.58 < 0.62`
4. → **Regra 2 casa: `FLOOR_TIER_WEAK`**

**Resultado:**
```python
{
    "should_escalate": True,
    "reason": "floor_tier_weak",
    "tier": "floor",
    "abstained": False,
    "top_evidence": 0.58,
    "connector_real": False,
}
```

### 5.4. Evidence Assembly

Monte o e-bundle para este state:

```python
state = {
    "connector_data": {
        "is_mock": False,
        "is_fallback": False,
        "source_system": "odata_sap",
        "message": "Timeout ao conectar",
        "error_code": "504",
    },
    "retrieved_context": [{"source": "odata_timeout_cpi.md", "rerank_score_calibrated": 0.68}],
    "diagnosis": {"llm_provider_used": "ollama/qwen3-coder-next:latest"},
}
```

**Solução:**
```python
evidence = [
    {
        "source_type": "connector",
        "trust_level": "system_observed",  # não mock, não fallback
        "locator": "odata_sap",
        "excerpt": "Timeout ao conectar",
        "retrieval_score": None,
        "rerank_score": None,
    },
    {
        "source_type": "rag",
        "trust_level": "retrieved_document",
        "locator": "odata_timeout_cpi.md",
        "excerpt": "...[chunk]...",
        "rerank_score_calibrated": 0.68,  # DA-42
        "retrieval_score": None,
    },
]

primary = [connector]  # system_observed
supporting = [rag]  # retrieved_document
```

---

## 6. Invariantes Críticas

1. **DA-22:** `supervisor_node` é 100% determinístico — NENHUMA chamada a LLM
2. **DA-33:** Rule Engine avaliado **antes** de LLM — se casar, `llm_provider_used = "rule_engine"`, `prompt_digest = None`
3. **DA-44:** Escalation usa SOMENTE fatos observáveis — `is_mock`, `collection`, `rerank_score_calibrated`, `matched_source` é None
4. **DA-16:** `_assemble_evidence()` **nunca** usa autoavaliação do LLM — apenas fatos observáveis do pipeline
5. **DA-42:** `rerank_score_calibrated` (sigmoid) é a única métrica comparável entre `sap_incident_docs` e `sap_reference_library`
6. **DA-53:** Quando rule engine resolve, `prompt_digest = None`, `prompt_version = None` — diagnostico sem LLM não tem prompt
7. **DA-57:** Web search é `fail-closed` — sem linha habilitada em `web_search_sources`, tool não existe
8. **DA-25:** `evidence_strength` + `model_confidence` → `diagnosis_confidence` (produto das duas)

---

## 7. Referências Rápidas

### 7.1. Arquivos-Chave

| Arquivo | Responsabilidade | DAs |
|---------|-----------------|-----|
| `app/agent/nodes.py` | 8+ nodes do grafo LangGraph | DA-22/33/44/55/57/58/59 |
| `app/agent/rules.py` | Rule Engine determinístico (21+ regras regex) | DA-33 |
| `app/agent/escalation.py` | Decisão de escalonamento baseada em sinais observáveis | DA-44 |
| `app/agent/state.py` | `CopilotState` (TypedDict), `DiagnosisModel` (Pydantic) | DA-22/53/55 |
| `app/agent/graph.py` | Definição do grafo LangGraph | DA-22/55/57/58/59 |
| `app/agent/supervisor.py` | Classificação domínio (SAP/SAAS/Generic, DA-22) | DA-22 |

### 7.2. Invariantes na Prática

| Invariante | Onde verificar | Exemplo de erro |
|------------|----------------|-----------------|
| Sem LLM no supervisor | `app/agent/supervisor.py` | uso de LLM em `classify_domain()` |
| Rule engine before LLM | `nodes.py::_run_diagnosis_agent()` (l.925-935) | chamada a LLM antes de `match_known_error()` |
| Escalation sem LLM | `escalaction.py::compute_escalation_signal()` | uso de `confidence` auto-relatada |
| Evidence sem autoavaliação | `nodes.py::_assemble_evidence()` (l.589-698) | leitura de `diagnosis["evidence"]` |
| `prompt_digest=None` para rule engine | `nodes.py::_run_diagnosis_agent()` (l.1069-1075) | `diagnosis.update(prompts.get_spec().provenance())` após return early |

### 7.3. Decisões de Arquitetura (DAs)

| DA | O que é | Onde |
|---|---------|------|
| DA-22 | Multi-agent: roteamento determinístico SAP/SAAS/Generic | `supervisor.py`, `graph.py` |
| DA-33 | Rule Engine determinístico (21+ regras, antes de LLM) | `rules.py`, `nodes.py` |
| DA-44 | Sinal determinístico de escalonamento (3 tiers) | `escalation.py` |
| DA-16/25 | Evidence/Trust Layer: `evidence_strength` calculado, não auto-relatado | `nodes.py::_assemble_evidence()` |
| DA-53 | Prompt versionado com `digest` SHA-256 | `prompts.py`, `nodes.py` |
| DA-57 | Web search: fail-closed, `approved` policy | `nodes.py::_web_search_allowed()`, `web_search_sources.py` |

---

**Próximos passos:** Continue para `DA_AULA_11_RULES.md` ou `DA_AULA_12_ESCALATION.md` para profundidade extra nos módulos Rule Engine e Escalation.
