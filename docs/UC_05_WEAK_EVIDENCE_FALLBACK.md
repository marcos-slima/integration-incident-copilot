# Use Case 5: Evidence Fraca → Reference Library Fallback

**Contexto:** Retrieval devolve todos hits com `score < 0.5` (ou nenhum hit) → evidence_strength baixa → capping de confiança

## Fluxo (DA-15, DA-17)

### 1. Retrieve Node
- Query: `description + connector.message`
- Qdrant hybrid search: dense + sparse (BM25) + RRF fusion
- Resultado: `hits = []` ou `hits = [{score: 0.3, ...}, {score: 0.4, ...}]`

### 2. Evidence Strength (app/agent/nodes.py:703–799)

```python
def _apply_confidence_guardrails(diagnosis: dict, state: CopilotState) -> dict:
    evidence_strength = 0.0
    # 1. Connector real (DA-15)
    data = state.get("connector_data")
    if data and not data.is_mock and not data.is_fallback:
        evidence_strength = 0.75  # mínimo para connector real
    # 2. Retrieved context (RAG)
    if evidence_strength < 1.0:
        top_hit = state.get("retrieved_context", [{}])[0]
        if top_hit:
            rerank_score = float(top_hit.get("rerank_score_calibrated", 0.0))
            evidence_strength = max(evidence_strength, rerank_score)
    # 3. Capping model_confidence
    model_confidence = diagnosis.get("model_confidence", diagnosis.get("confidence", 0.5))
    evidence_ceiling = evidence_strength + 0.25
    if model_confidence > evidence_ceiling:
        model_confidence = evidence_ceiling
        diagnosis["matched_source"] = None  # desacredita source se confidence baixo
    diagnosis["evidence_strength"] = evidence_strength
    diagnosis["model_confidence"] = model_confidence
    diagnosis["diagnosis_confidence"] = evidence_strength * model_confidence
    return diagnosis
```

### 3. Cenários

#### Cenário A: Nenhum hit RAG
```python
state = {
    "retrieved_context": [],
    "connector_data": None,
}
# evidence_strength = 0.0
# model_confidence capped em 0.3 (min)
# diagnosis_confidence = 0.0 * 0.3 = 0.0
```

#### Cenário B: Hit RAG com score baixo
```python
state = {
    "retrieved_context": [{"rerank_score_calibrated": 0.42}],
    "connector_data": None,
}
# evidence_strength = 0.42 (do RAG)
# model_confidence capped em 0.67 (0.42 + 0.25)
# diagnosis_confidence = 0.42 × 0.67 ≈ 0.28
```

#### Cenário C: Connector real + hit RAG baixo
```python
state = {
    "retrieved_context": [{"rerank_score_calibrated": 0.35}],
    "connector_data": ConnectorResult(..., is_mock=False, is_fallback=False),
}
# evidence_strength = 0.75 (mínimo connector real, não sobrescreve RAG se maior)
# model_confidence capped em 1.0 (0.75 + 0.25)
# diagnosis_confidence = 0.75 × model_confidence (ex: 0.9 × 0.75 = 0.675)
```

### 4. Fallback para Reference Library (DA-17 — NÃO IMPLEMENTADO)

**Planejado (não codificado ainda):**

```python
# app/agent/nodes.py::retrieve_node()
if not state.get("retrieved_context") or top_score < 0.3:
    # fallback para reference_library (documentation pura, não incidentes)
    reference_hits = retrieve(
        query,
        target="reference_library",
        top_k=3
    )
    return {"retrieved_context": reference_hits}
```

**Diferença de `incidents` vs `reference_library`:**
- `incidents`: casos resolvidos (ticket → root_cause)
- `reference_library`: documentação técnica pura (how-to, troubleshooting steps)

**Benefício:** Se nenhum incidente similar encontrado, fallback para documentação pode ajudar o LLM a inferir solução genérica.

### 5. Mensagem ao Usuário (DA-15)

```python
# app/agent/nodes.py:728–746
if not state.get("retrieved_context") and not data:
    capped = min(model_confidence, 0.3)
    if capped < model_confidence:
        model_confidence = capped
        diagnosis["matched_source"] = None
        diagnosis["probable_root_cause"] = (
            f"[confianca limitada - nenhum documento relevante encontrado] "
            f"{diagnosis.get('probable_root_cause', '')}"
        )
```

**Exemplo de saída:**
```json
{
  "probable_root_cause": "[confianca limitada - nenhum documento relevante encontrado] O problema pode ser related to authentication...",
  "model_confidence": 0.28,
  "evidence_strength": 0.0,
  "diagnosis_confidence": 0.0,
  "matched_source": null
}
```
