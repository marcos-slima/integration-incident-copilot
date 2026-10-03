# Use Case 4: Rule Engine (Sem LLM)

**Contexto:** Descrição bate com regra SAP determinística → diagnóstico sem chamada ao LLM (DA-33)

## Fluxo (Rule Engine)

### 1. Supervisor
```python
# app/agent/supervisor.py::classify_domain()
# Se `interface_type` em `_SAP_KEYWORDS` ou `interface_type="odata"`/`"rfc"` → `agent_domain="sap"`
```

### 2. Rule Engine Node (app/agent/rules.py:1–200)

```python
# app/agent/rules.py::match_known_error()
def match_known_error(description: str) -> Optional[RuleMatch]:
    """Match com 21 regras determinísticas SAP."""

    # Regras de exemplo:
    # 1. IDoc stuck
    if "idoc" in description.lower() and "stuck" in description.lower():
        return RuleMatch(
            rule_id="IDOC_STUCK",
            description="IDoc travado no status 51 (Processamento)",
            probable_root_cause="Conector RFC ou OData com erro de commit/rollback não tratado",
            recommended_actions=[
                "Verificar status do IDoc no transaction WE02/WE05",
                "Consultar log do gateway (transaction SMGW)",
                "Verificar credenciais e endpoint do receptor"
            ],
            confidence=0.95
        )

    # 2. RFC destination not found
    if "rfc" in description.lower() and "destination" in description.lower() and "not found" in description.lower():
        return RuleMatch(
            rule_id="RFC_DEST_NOT_FOUND",
            description="RFC Destination não encontrado",
            probable_root_cause="Destino RFC não registrado no transaction SM59 ou inválido",
            recommended_actions=[
                "Verificar transaction SM59 (RFC Destinations)",
                "Testar conexão via 'Test Connection' no SM59",
                "Validar usuário/senha do destino"
            ],
            confidence=0.93
        )

    # 3. Authentication failed
    if ("authentication" in description.lower() or "unauthorized" in description.lower()) and ("sap" in description.lower() or "rfc" in description.lower()):
        return RuleMatch(
            rule_id="AUTH_FAILED",
            description="Falha de autenticação SAP",
            probable_root_cause="Credentials inválidas ou token expirado",
            recommended_actions=[
                "Verificar usuário e senha no conector configuration",
                "Renovar token OAuth2 se aplicável",
                "Verificar expiration do certificado (RFC: user凭证)"
            ],
            confidence=0.92
        )

    # 4. Network timeout
    if "timeout" in description.lower() and ("network" in description.lower() or "connection" in description.lower()):
        return RuleMatch(
            rule_id="NETWORK_TIMEOUT",
            description="Timeout de conexão",
            probable_root_cause="Latência alta ou firewall bloqueando portas SAP",
            recommended_actions=[
                "Verificar latência com transaction SMGP",
                "Consultar network team sobre firewall rules",
                "Aumentar timeout no conector se latência for normal"
            ],
            confidence=0.90
        )

    # 5. OData error
    if "odata" in description.lower() and "error" in description.lower():
        return RuleMatch(
            rule_id="ODATA_ERROR",
            description="OData request error",
            probable_root_cause="Metadata $metadata inválido ou endpoint alterado (drift de contrato)",
            recommended_actions=[
                "Verificar $metadata no browser (browser test)",
                "Comparar com contract baseline (DA-52)",
                "Consultar SAP Basis sobre changes no backend"
            ],
            confidence=0.88
        )

    # ... (21 regras no total)

    return None
```

### 3. Rules Node (app/agent/nodes.py:76–128)

```python
# app/agent/nodes.py::rules_node()
def rules_node(state: CopilotState) -> CopilotState:
    """Tenta match com regras determinísticas antes de LLM."""
    if not settings.rule_engine_enabled:
        return state  # Skip se desligado (apenas para testes)

    description = state.get("description", "")
    match = match_known_error(description)

    if match:
        return {
            "rule_match": match,
            "diagnosis": {
                "probable_root_cause": match.probable_root_cause,
                "recommended_actions": match.recommended_actions,
                "evidence_strength": 1.0,  # Regra determinística
                "model_confidence": match.confidence,  # 0.88–0.95
                "diagnosis_confidence": match.confidence,
                "matched_rule": match.rule_id
            },
            "llm_used": False  # Sem LLM
        }

    return state  # Sem match, proceed para LLM
```

## Exemplo de Diagnóstico (Rule Match)

### Input
```json
{
  "description": "IDoc stuck in status 51 after RFC call",
  "interface_type": "odata",
  "identifier": "EDIDOC-456789"
}
```

### Output (Rule Match IDOC_STUCK)
```json
{
  "rule_match": {
    "rule_id": "IDOC_STUCK",
    "description": "IDoc travado no status 51 (Processamento)",
    "probable_root_cause": "Conector RFC ou OData com erro de commit/rollback não tratado",
    "recommended_actions": [
      "Verificar status do IDoc no transaction WE02/WE05",
      "Consultar log do gateway (transaction SMGW)",
      "Verificar credenciais e endpoint do receptor"
    ],
    "confidence": 0.95
  },
  "diagnosis": {
    "probable_root_cause": "Conector RFC ou OData com erro de commit/rollback não tratado",
    "recommended_actions": [
      "Verificar status do IDoc no transaction WE02/WE05",
      "Consultar log do gateway (transaction SMGW)",
      "Verificar credenciais e endpoint do receptor"
    ],
    "evidence_strength": 1.0,
    "model_confidence": 0.95,
    "diagnosis_confidence": 0.95,
    "matched_rule": "IDOC_STUCK"
  },
  "llm_used": false
}
```

### Response (sem LLM)
```json
{
  "incident_id": "INC-12345",
  "probable_root_cause": "Conector RFC ou OData com erro de commit/rollback não tratado",
  "recommended_actions": [
    "Verificar status do IDoc no transaction WE02/WE05",
    "Consultar log do gateway (transaction SMGW)",
    "Verificar credenciais e endpoint do receptor"
  ],
  "llm_provider_used": null,
  "prompt_version": "desconhecido",
  "prompt_digest": null,
  "evidence_strength": 1.0,
  "model_confidence": 0.95,
  "diagnosis_confidence": 0.95
}
```

## DA-33: 21 Regras Implementadas

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

## DA-33: Guardrails no Rule Engine

- **Rule engine sempre primeiro** → sem LLM se match
- **LLM never called if match** → `llm_used=false`, `prompt_version/prompt_digest=null`
- **Evidence strength = 1.0** → confiança máxima (determinístico)
- **Rules node pode ser desligado** → `rule_engine_enabled=False` (apenas testes)

## DA-33: Performance

**Rule engine:**
- Latência: ~1ms (regex + dict lookup)
- Sem token consumption
- Sem cold-start latency

**Comparação:**
- Rule engine match: ~1ms (determinístico)
- Ollama: ~2000ms (cold-start + inference)
- Cloud: ~5000ms (network + cloud inference)

## DA-33: Exceções para LLM

Se rule engine **não match**, proceed para LLM:
```python
# app/agent/nodes.py
if not state.get("rule_match"):
    # LLM node (sap_diagnose_node, saas_diagnose_node, generic_diagnose_node)
    return llm_node(state)
```

**Exemplo:**
```json
{
  "description": "HTTP 500 ao chamar BAPI_MATERIAL_SAVEDATA"
}
```

- Rule engine: não bate com nenhuma regra (detalhe BAPI não é SAP-specific key)
- LLM: chamado, usa contexto RAG + web search
- Resultado:LLM inferência

## DA-33: Teste

```python
# tests/test_rules.py
def test_match_idoc_stuck():
    match = match_known_error("IDoc stuck in status 51")
    assert match.rule_id == "IDOC_STUCK"
    assert match.confidence == 0.95
```

## DA-33: Limitações

1. **Regex only** → não entende semântica complexa
2. **Order matters** → regras mais específicas primeiro
3. **Manual updates** → nova regra require PR + deploy
4. **No learning** → não melhora com dados novos (precisa update manual)

**Por isso rule engine não substitui LLM:** é um pre-filter determinístico para erros conhecidos.
