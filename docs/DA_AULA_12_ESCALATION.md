# DA-AULA 12: Signal de Escalonamento (DA-44)

## Objetivo da Aula

Compreender o sistema determinístico de escalonamento do Integration Incident Copilot (DA-44): 5 estados observáveis, thresholds configuráveis, decisão sem LLM. Ao final, você será capaz de:
- Explicar os 5 estados de escalonamento e suas causas
- Calcular o sinal de escalonamento usando apenas fatos observáveis
- Derivar as decisões com base em thresholds reais do código

---

## 1. O Problema que Resolve

### 1.1. Contexto

Para o **tier 3** (modelo pago: GPT/Claude/Gemini), o pipeline precisa de um gatilho: quando a resposta do modelo local não é boa o bastante, escalar.

**Hipótese inicial (errada):** `evidence_strength` como sinal de escalonamento.

**Por que NÃO serve (DA-44, item "Problema"):**

1. **PISO QUE SATURA** — `nodes.py::_compute_evidence_strength()` faz `strength = max(rag_score, 0.75)` com conector real. Portanto, `evidence_strength` **nunca** fica abaixo de `0.75` no caminho de produção — qualquer limiar acima disso **nunca dispara** com conector real.

2. **NÃO SABE DE QUE TIER VEIO A EVIDÊNCIA** — O mesmo número significa coisas diferentes conforme a origem. `sap_incident_docs` (40 chunks curados) e `sap_reference_library` (28.962 chunks genéricos) passam pelo MESMO reranker e escala de sigmoid (DA-42), mas não têm o mesmo peso probatório.

3. **SATURA E NÃO DISCRIMINA** — Mede "quanto contexto existe", não "o modelo acertou". Um modelo que alucina com confiança alta recebe o mesmo `evidence_strength` que um que acerta.

### 1.2. Solução (DA-44)

Sinal **NOVO**, aditivo, que decide escalonamento. **NÃO altera** `evidence_strength` em nada — seria contrário à DA-16 (`_assemble_evidence()` nunca usa autoavaliação do LLM).

O sinal é derivado de **APENAS fatosObserváveis e já decididos em código:**

- `connector_data.is_mock` / `.is_fallback` (`base.py`)
- `hit["collection"]` (`retriever.py`, l.182/209)
- `hit["rerank_score_calibrated"]` (`retriever.py`, DA-42)
- `diagnosis["matched_source"] is None` (`nodes.py`, l.740-767: guardrail anula fonte quando não está entre recuperados)

---

## 2. Os 5 Estados de Escalonamento

### 2.1. Catálogo de Razões ( DA-44)

**Arquivo:** `app/agent/escalation.py::Reason` (l.129-144)

| Estado | Código | Quando dispara? | Escala? | Descrição |
|--------|--------|-----------------|---------|-----------|
| **GROUNDED** | `grounded` | Evidence do tier curado ≥ 0.45 | ❌ | Base probatória sólida |
| **NO_CONTEXT** | `no_context` | Nem conector nem chunk recuperado | ❌ | Sem contexto nenhum |
| **ABSTAINED** | `abstained` | Guardrail anulou `matched_source` | ✅ | Convicção sem lastro (DA-25) |
| **FLOOR_TIER_WEAK** | `floor_tier_weak` | Tier de piso + evidence < 0.62 | ✅ | Chunk genérico fraco |
| **CURATED_TIER_WEAK** | `curated_tier_weak` | Chunk curado + evidence < 0.45 | ✅ | Curado mas rerank fraco |

### 2.2. Thresholds

**Arquivo:** `app/agent/escalation.py::thresholds` (l.121, 126)

```python
FLOOR_TIER_MIN_EVIDENCE = 0.62  # Tier de piso (manuais genéricos)
CURATED_TIER_MIN_EVIDENCE = 0.45  # Tier curado (incident docs)
```

**Observação (DA-44, "Limitações"):**
- `FLOOR_TIER_MIN_EVIDENCE` e `CURATED_TIER_MIN_EVIDENCE` **NÃO** foram calibrados contra o corpus real de 28.962 chunks — são pontos de partida, não constantes validadas.

---

## 3. Algoritmo de Decisão

### 3.1. Ordem das Regras

**Arquivo:** `app/agent/escalation.py::compute_escalation_signal()` (l.199-281)

A primeira regra que casa define o estado. Ordem do mais forte ao mais fraco:

```
1. Sem contexto (NO_CONTEXT)
   └─ Se não tem connector_data E não tem hit → escala=False
      (modelo maior cria alucinação, não evidência)

2. Abstenção (ABSTAINED)
   └─ Guardrail anulou matched_source → escala=True

3. Tier de piso fraco (FLOOR_TIER_WEAK)
   └─ collection == "sap_reference_library"
      AND rerank_score_calibrated < 0.62 → escala=True

4. Tier curado fraco (CURATED_TIER_WEAK)
   └─ collection == "sap_incident_docs" (ou "")
      AND rerank_score_calibrated < 0.45 → escala=True

5. Evidenceado (GROUNDED)
   └─ Todos os anteriores falharam → escala=False
```

### 3.2. Função determinística

```python
def compute_escalation_signal(state) -> EscalationDecision:
    """Determinista por construção: le facts do state, não chama LLM."""
```

**Invariante DA-44:** _"sinal determinístico baseado em fatos observáveis (sem LLM)"_

---

## 4. Exemplos Reais (DA-44, "Validação Empírica")

### 4.1. Caso que motivou a DA

**Input:**
```python
# Query: "algo estranho aconteceu", odata, identifier XPTO-999-NAO-EXISTES
# Retorno: top_hit com collection="sap_reference_library",
#          rerank_score_calibrated=0.383 (< 0.45)
```

**Resultado:**
- `matched_source='odata_timeout_cpi.md'` (documento recuperado)
- `top_evidence=0.383` (rerank, DA-42)
- `collection="sap_reference_library"` (tier de piso)
- `abstained=False` (guardrail não anulou — documento ESTAVA recuperado)

**Regra que casou:** Regra 3 (`FLOOR_TIER_WEAK`)

**Decisão:**
```python
EscalationDecision(
    should_escalate=True,
    reason="floor_tier_weak",
    tier="floor",
    abstained=False,
    top_evidence=0.383,
    connector_real=False,
)
```

**Conclusão:** **Escalou** — a regra de abstenção **não** disparou (não foi o problema), mas o tier de piso com evidence fraca **sim**. Se só tivesse abstenção, o erro passaria.

### 4.2. Controle (IDoc status 51)

**Input:**
```python
# rfc, RFC-IDOC-51-DEMO
# Retorno: matched_source='rule_engine:sap_idoc_status_51',
#          top_evidence=1.000 (rule engine)
```

**Resultado:**
- `matched_source='rule_engine:sap_idoc_status_51'`
- `top_evidence=1.000`
- `collection=""` (não veio de RAG)
- `connector_real=False` (RFC simulator)

**Regra que casou:** Regra 4 (`GROUNDED` — não tem evidência fraca)

**Decisão:**
```python
EscalationDecision(
    should_escalate=False,
    reason="grounded",
    tier="curated",  # default quando collection=""
    abstained=False,
    top_evidence=1.000,
    connector_real=False,
)
```

**Conclusão:** **Não escalou** — rule engine resolveu antes do RAG, evidência máxima.

---

## 5. Fluxo Completo no Pipeline

### 5.1. Onde `compute_escalation_signal()` é chamado

**Arquivo:** `app/agent/graph.py` (via LangGraph)

O escalonamento é computado **ANTES** de chamar o tier 3 (cloud premium). Se `should_escalate=False`, continua com LLM local (Ollama). Se `True`, invoca provider pago (via AI Gateway, DA-26/43).

### 5.2. Diagrama de decisões

```
┌────────────────────────────────────────────────────┐
│ state (connector_data, retrieved_context, diagnosis│
└────────────────────────────────────────────────────┘
                     │
                     ▼
        ┌───────────────────────┐
        │ compute_escalation_   │
        │ signal(state)         │
        └───────────────────────┘
                     │
        ┌────────────┴────────────┐
        │                         │
        ▼                         ▼
  has_context?              NO_CONTEXT
  (connector ou hit)        (escala=False)
        │
   ┌────────┴────────┐
   │                 │
   ▼                 ▼
abstained?      FLOOR_TIER_WEAK         CURATED_TIER_WEAK    GROUNDED
(matched_source None)  (score < 0.62)     (score < 0.45)     (resto)
   │                 │                      │                  │
   ▼                 ▼                      ▼                  ▼
escala=True     escala=True            escala=True        escala=False
(tier=ungrounded) (tier=floor)         (tier=curated_weak) (tier=floor/curated)
```

---

## 6. Escalona PARA Onde?

### 6.1. `EscalationDecision.escalate_to`

**Arquivo:** `app/agent/escalation.py::EscalationDecision.escalate_to` (l.159-167)

```python
@property
def escalate_to(self) -> str | None:
    """Tier de destino quando ha escalonamento. None quando nao ha."""
    return "cloud_premium" if self.should_escalate else None
```

**Observação (DA-44, "Limitação"):**
- O tier 3 (invocação do modelo pago) **NÃO** faz parte desta DA.
- Este módulo decide **APENAS se há caso para escalar**.
- Quem invoca passa pelo **AI Gateway** (DA-26) e responde à política de `data_sovereignty_mode` / `CONFIDENTIAL_ALLOWED_ORIGINS` (DA-43).

---

## 7. Log/auditoria

### 7.1. `as_log_fields()`

**Arquivo:** `app/agent/escalation.py::EscalationDecision.as_log_fields()` (l.169-179)

```python
def as_log_fields(self) -> dict:
    """Campos para log/auditoria. Sem conteúdo de documento e sem PII."""
    return {
        "escalation": self.reason,  # "grounded", "abstained", ...
        "should_escalate": self.should_escalate,
        "tier": self.tier,  # "none", "ungrounded", "floor", ...
        "abstained": self.abstained,
        "top_evidence": round(self.top_evidence, 3),
        "connector_real": self.connector_real,
    }
```

**Exemplo de log:**
```json
{
  "escalation": "floor_tier_weak",
  "should_escalate": true,
  "tier": "floor",
  "abstained": false,
  "top_evidence": 0.383,
  "connector_real": false
}
```

---

## 8. Exercícios Práticos

### 8.1. Estado de escalonamento

Para cada caso abaixo, indique:
1. Estado (código da razão)
2. `should_escalate`
3. `tier`

**A)** Não tem conector_data, não tem retrieved_context
**B)** `matched_source = None` (guardrail anulou)
**C)** `collection="sap_reference_library", rerank_score_calibrated=0.70`
**D)** `collection="sap_incident_docs", rerank_score_calibrated=0.30`
**E)** `collection="sap_reference_library", rerank_score_calibrated=0.62`

**Solução:**
- **A)** `no_context`, `should_escalate=False`, `tier="none"`
- **B)** `abstained`, `should_escalate=True`, `tier="ungrounded"`
- **C)** `grounded` (0.70 ≥ 0.62), `should_escalate=False`, `tier="floor"`
- **D)** `curated_tier_weak` (0.30 < 0.45), `should_escalate=True`, `tier="curated_weak"`
- **E)** `grounded` (0.62 ≥ 0.62), `should_escalate=False`, `tier="floor"` (limite inclusive)

### 8.2. Ordem das regras

**Pergunta:** Por que a regra de **sem contexto** vem PRIMEIRO?

**Resposta:** Porque modelo maior **não cria evidência que não existe**. Se nem conector nem chunk estão presentes, o que o tier 3 produziria seria **alucinação mais confiante**, não resposta melhor. Pior para o engenheiro de suporte do que "dados insuficientes".

### 8.3. `abstained` vs `floor_tier_weak`

**Cenário:** `matched_source=None` (guardrail anulou) e `rerank_score_calibrated=0.10`.

Esse caso pode casar **duas** regras (abstenção e tier fraco). Por que **não** há conflito?

**Resposta:** Porque **regra 2 (abstenção)** vem primeiro. Primeira regra que casa define o estado. Abstenção é uma falha **mais forte** (convicção sem lastro), então tem precedência sobre `floor_tier_weak`.

---

## 9. Invariantes Críticas

1. **DA-44:** Sinal determinístico baseado em fatos observáveis, **sem LLM**
2. **NUNCA chama LLM para decidir escalonamento** — só le estado pronto
3. **Thresholds são lineares e comparáveis** — `rerank_score_calibrated` (DA-42) na escala de sigmoid
4. **`evidence_strength` não é usado** — seu piso de `0.75` com conector real o tornaria ineficaz

---

## 10. Limitações Conhecidas

| Limitação | Detalhe |
|-----------|---------|
| Thresholds não calibrados | `FLOOR_TIER_MIN_EVIDENCE` e `CURATED_TIER_MIN_EVIDENCE` são pontos de partida, não validados contra corpus real |
| Tier 3 não implementado | Este módulo só decide "devo escalar?" — quem invoca o tier 3 passa pelo AI Gateway (DA-26/43) |
| Sem calibração de embeddings | `REFERENCE_FALLBACK_THRESHOLD` recalibrado em 2026-10-01 para `nomic-embed-text` —trocar modelo invalida valor |

---

## 11. Referências Rápidas

### 11.1. Arquivos-Chave

| Arquivo | Responsabilidade | DAs |
|---------|-----------------|-----|
| `app/agent/escalation.py` | Sinal determinístico de escalonamento (5 estados) | DA-44 |
| `app/agent/nodes.py` | `_compute_evidence_strength()` (piso de 0.75 com conector real) | DA-15/25 |
| `app/agent/graph.py` | Invoca escalonação antes de tier 3 | DA-26/44 |

### 11.2. DAs Relacionadas

| DA | Resumo |
|----|--------|
| DA-15/25 | Evidence/Trust Layer: guardrail anula `matched_source` quando não está entre recuperados |
| DA-42 | Escala de sigmoid para `rerank_score` (daí thresholds são lineares) |
| DA-26/43 | AI Gateway: quem invoca tier 3, aplica política de soberania |

### 11.3. Valores Críticos

```python
FLOOR_TIER_MIN_EVIDENCE = 0.62  # Tier de piso (manuais genéricos)
CURATED_TIER_MIN_EVIDENCE = 0.45  # Tier curado (incident docs)
# Ambos usando rerank_score_calibrated (DA-42)
```

---

**Próximos passos:** Continue para `DA_AULA_13_MCP_A2A_EVENTS.md` para cobrir transportes REST, MCP tools, JSON-RPC A2A, CloudEvents e AMQP 1.0.
