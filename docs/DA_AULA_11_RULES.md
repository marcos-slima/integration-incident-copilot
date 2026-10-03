# DA-AULA 11: Rule Engine (DA-33)

## Objetivo da Aula

Compreender o Rule Engine determinístico do Integration Incident Copilot: 21+ regras regex para erros SAP/integração conhecidos, Avaliação antes de LLM ( DA-33), e como ele integra com o pipeline LangGraph. Ao final, você será capaz de:
- Localizar e explicar cada uma das 21 regras do catálogo
- Explicar por que o Rule Engine é avaliado ANTES de LLM
- Derivar o `evidence_strength` com base em `has_connector_data`

---

## 1. O Rule Engine: Camada Zero de Custo

### 1.1. Problema que resolve

**Dado:** 60–70% dos incidentes de integração têm padrões conhecidos (revisão arquitetural externa, Fase 12).

**Consequência de chamar LLM para tudo:**
- **Custo:** Tokens desnecessários por pattern matching
- **Latência:** Espera pelo tempo de resposta do modelo
- **Consistência:** Mesma causa pode ser explicada de formas diferentes

**Solução:** Camada zero — regras determinísticas avaliadas **ANTES** de chamar LLM.

**Invariante DA-33:** _"Rule Engine determinístico — camada zero de custo, avaliada ANTES de LLM"_

### 1.2. Arquitetura do fluxo

```
┌─────────────────────────────┐
│   supervisor_node           │ → classifica agent_domain
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│   connector_node            │ → connector_data (status, error_code, is_mock)
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│   match_known_error()       │ ← Rule Engine (antes de LLM!)
└──────────────┬──────────────┘
               │
               ├─ MATCH ⇒ diagnosis (confidence=0.90, llm_provider_used="rule_engine")
               │
               ▼ NO MATCH
┌─────────────────────────────┐
│   LLM (ReAct, structured)   │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│   report_node               │
└─────────────────────────────┘
```

---

## 2. O Catálogo de Regras (21 patterns)

### 2.1. Estrutura da regra

**Arquivo:** `app/agent/rules.py::ErrorRule`

```python
@dataclass
class ErrorRule:
    patterns: list[str]          # Regex list, re.IGNORECASE aplicado
    probable_root_cause: str     # Explicação determinística
    next_steps: list[str]        # Ações concretas para operador
    category: str                # Agrupamento para logs/métricas
    confidence: float = 0.90     # Alta certeza por pattern matching
```

**Objetivo:** Quando um padrão bate com `description` ou `connector_data.message`, devolve **diagnóstico completo sem LLM**.

### 2.2. Regras por categoria

**TOTAL: 21 regras** (da linha 48 a 523 de `rules.py`)

| Categoria | Patterns (regex) | Erro SAP/integração |
|-----------|------------------|---------------------|
| `auth_oauth_expired` | `"oauth.*token.*expir"`, `"401.*unauthorized"`, `"JWT.*expir"` | Token OAuth2 expirado |
| `auth_forbidden` | `"403.*forbidden"`, `"authorization.*failed"`, `"SY-SUBRC.*4.*authoriz"` | Erro de autorização (403) |
| `sap_material_lock` | `"M8082"`, `"material.*lock"`, `"ENQUEUE.*material"` | Material bloqueado (M8082) |
| `sap_pricing_condition_missing` | `"VK041"`, `"condition.*record.*missing"`, `"NO PRICING CONDITION"` | Condição de preço ausente |
| `sap_idoc_status_51` | `"IDoc.*status.*51"`, `"IDOC.*APPLICATION.*ERROR"`, `"WE19.*status.*51"` | IDoc travado (status 51) |
| `sap_idoc_status_26` | `"IDoc.*status.*26"`, `"IDOC.*ALE.*ERROR"` | IDoc com erro de sintaxe (status 26) |
| `http_503_unavailable` | `"HTTP.*503"`, `"503.*Service.*Unavailable"` | Serviço indisponível (503) |
| `http_timeout` | `"HTTP.*504"`, `"504.*Gateway.*Timeout"`, `"socket.*timeout"` | Timeout de conexão |
| `network_connection_refused` | `"connection.*refused"`, `"conex[aã]o.*recusad"`, `"partner.*not.*reached"` | Conexão recusada ou host inacessível |
| `cpi_mapping_error` | `"MAPPING_FAIL"`, `"xslt.*error"`, `"groovy.*script.*error"` | Erro de mapeamento/script no CPI |
| `ssl_certificate_expired` | `"certificate.*expired"`, `"SSL.*expired"`, `"PKIX.*path.*build.*failed"` | Certificado SSL expirado |
| `rfc_destination_error` | `"RFC.*destination.*not.*found"`, `"RFC.*logon.*failed"` | Destino RFC não encontrado |
| `rate_limit_exceeded` | `"429.*too.*many.*request"`, `"rate.*limit.*exceeded"`, `"throttl"` | Limite de requisições excedido |
| `duplicate_document` | `"duplicate.*entry"`, `"unique.*constraint"`, `"ja.*existe"` | Documento duplicado (violação de unicidade) |
| `sap_idoc_multiple_objects` | `"IDOC_ERROR_MULTIPLE_OBJECTS"`, `"multiple.*objects.*idoc"` | IDoc com múltiplos objetos |
| `sap_idoc_port_partner` | `"IDoc.*status.*68"`, `"port.*not.*found.*partner"` | Erro de parceiro/porta (status 68) |
| `sap_badi_exception` | `"BAdI.*exception"`, `"IF_EX_.*=>.*exception"`, `"CX_BADI"` | Exceção em BAdI customizado |
| `sap_bapi_failure` | `"BAPI.*RETURN.*E"`, `"BAPI.*failure"`, `"BAPIRET.*TYPE.*E"` | BAPI retornou erro (type='E') |
| `sap_serial_number_duplicate` | `"serial.*number.*duplicate"`, `"numero.*serie.*duplicado"` | Número de série duplicado |
| `sap_sd_credit_block` | `"credit.*block"`, `"bloqueio.*credito"`, `"VKM1"` | Pedido bloqueado por crédito |
| `sap_mdg_mdi_lock` | `"MDG.*lock"`, `"MDI.*replicate.*fail"`, `"BP.*lock.*governance"` | Erro MDG/MDI (master data) |

**Total de patterns:** ~60 (soma de todos os `patterns` em todas as regras)

### 2.3. Exemplos de uso real

**Exemplo 1: IDoc status 51**
```python
# Input:
text = "IDoc 456789 em status 51: WE19 mostrar erro"

# match_known_error(text, has_connector_data=True)
# → Retorna:
{
    "matched_source": "rule_engine:sap_idoc_status_51",
    "probable_root_cause": "IDoc com status 51 (Application Document Not Posted)...",
    "next_steps": ["Analisar o IDoc em WE02/WE05...", ...],
    "confidence": 0.90,
    "rule_engine_category": "sap_idoc_status_51",
    "llm_provider_used": "rule_engine",
    "evidence_strength": 0.95  # (has_connector_data=True)
}
```

**Exemplo 2: Token OAuth expirado**
```python
# Input:
text = "HTTP 401 unauthorized, OAuth token expired"

# match_known_error(text, has_connector_data=False)
# → Retorna:
{
    "matched_source": "rule_engine:auth_oauth_expired",
    "probable_root_cause": "Token OAuth2 expirado ou inválido...",
    "next_steps": ["Renovar o token OAuth2...", ...],
    "confidence": 0.90,
    "rule_engine_category": "auth_oauth_expired",
    "llm_provider_used": "rule_engine",
    "evidence_strength": 0.70  # (has_connector_data=False)
}
```

---

## 3. Integração com o Pipeline LangGraph

### 3.1. Onde o Rule Engine é chamado

**Arquivo:** `app/agent/nodes.py::_run_diagnosis_agent()` (l.925-935)

```python
def _run_diagnosis_agent(state: CopilotState, persona: str) -> dict:
    """Executa o agente ReAct com Rule Engine antes de chamar LLM."""

    # DA-33: Rule Engine DETERMINÍSTICO (antes de LLM!)
    diagnosis = match_known_error(
        state.get("description", ""),
        has_connector_data=bool(state.get("connector_data") and
                               not state["connector_data"].is_mock and
                               not state["connector_data"].is_fallback)
    )

    if diagnosis:
        # Regra casou → devolve diagnosis SEMPARE LLM
        return {
            **diagnosis,
            "prompt_version": None,   # NÃO tem prompt!
            "prompt_digest": None,    # NÃO tem digest!
        }

    # Nenhuma regra casou → seguir para LLM (ReAct)
    ...
```

**Invariante DA-33:** _"Rule Engine avaliado ANTES de qualquer chamada ao LLM"_

### 3.2. Comportamento após match

**Se regra casar:**
1. Devolve `diagnosis` com `llm_provider_used = "rule_engine"`
2. `prompt_version = None`, `prompt_digest = None` (DA-53)
3. **Nenhum token do LLM consumido**
4. `evidence_strength = 0.95` se `connector_data` real, `0.70` se só texto

**Se nenhuma regra casar:**
1. Continua para `ReAct` com LLM (OLLAMA → cloud fallback)
2. `llm_provider_used = "ollama"` (ou provider cloud)
3. `prompt_version` e `prompt_digest` do artefato versionado (DA-53)

### 3.3. Exemplo completo

**Pipeline com Rule Engine ativo:**
```python
# Input:
state = {
    "agent_domain": "sap",
    "description": "IDoc status 51, checar WE02",
    "connector_data": {
        "status": "error",
        "error_code": "IDOC_STATUS_51",
        "is_mock": False,
        "is_fallback": False
    }
}

# Execução:
supervisor_node → agent_domain = "sap"
connector_node → connector_data = {...} (real)
retrieve_node → (opcional)
diagnosis_node:
  ├─ match_known_error("IDoc status 51") → MATCH
  ├─ return diagnosis (sem LLM!)
  └─ llm_provider_used = "rule_engine"

# Output:
diagnosis = {
    "matched_source": "rule_engine:sap_idoc_status_51",
    "probable_root_cause": "IDoc com status 51...",
    "confidence": 0.90,
    "llm_provider_used": "rule_engine",  # ← NÃO chamou LLM!
    "prompt_version": None,              # ← NÃO tem prompt!
    "evidence_strength": 0.95            # ← connector_data real
}
```

---

## 4. Cálculo de `evidence_strength`

### 4.1. Fórmula

**Arquivo:** `app/agent/rules.py::match_known_error()` (l.548)

```python
def match_known_error(text: str, has_connector_data: bool = False) -> dict | None:
    evidence_strength = 0.95 if has_connector_data else 0.70
    ...
```

### 4.2. Justificativa

| Situação | `has_connector_data` | `evidence_strength` | Justificativa |
|----------|----------------------|---------------------|---------------|
| Regra bate + conector real | `True` | `0.95` | Source observável direta (o sistema reportou o erro) |
| Regra bate + só texto | `False` | `0.70` | Baseado apenas no texto digitado (mais frágil) |

**Observação:** O valor `0.95` **não é o teto** do guardrail (`evidence_strength + 0.25 = 1.20` clamped a `1.0`). A regra já entrega alta confiança por si só.

---

## 5. Exercícios Práticos

### 5.1. Identificação de regras

Para cada descrição abaixo, indique:
1. Qual regra casa (ou se nenhuma casa)?
2. `llm_provider_used` no diagnosis?
3. `evidence_strength` aproximado?

**A)** "HTTP 504 Gateway Timeout ao chamar OData"
**B)** "Material 12345 bloqueado (M8082)"
**C)** "Erro desconhecido no processamento"
**D)** "403 Forbidden: SY-SUBRC 4 authorization error"
**E)** "IDoc 987654321 status 26, error during syntax check"

**Solução:**
- **A)** `http_timeout`, `llm_provider_used = "rule_engine"`, `evidence_strength = 0.95` (se connector real)
- **B)** `sap_material_lock`, `llm_provider_used = "rule_engine"`, `evidence_strength = 0.95`
- **C)** Nenhuma regra casa → LLM necessário
- **D)** `auth_forbidden`, `llm_provider_used = "rule_engine"`, `evidence_strength = 0.95`
- **E)** `sap_idoc_status_26`, `llm_provider_used = "rule_engine"`, `evidence_strength = 0.95`

### 5.2. Ordem das regras

**Pergunta:** Por que as regras estão ordenadas por "especificidade" (mais específicas primeiro)?

**Resposta:** Primeira regra que casa define o diagnóstico. Se `IDoc status 51` casa tanto em `sap_idoc_status_51` quanto em uma regra genérica `idoc_error`, a mais específica deve vir primeiro para evitar coincidência parcial.

### 5.3. `has_connector_data` em prática

Considere dois casos:

**Caso 1:**
```python
text = "IDoc status 51"
has_connector_data = False  # usuário digitou só o texto
```

**Caso 2:**
```python
text = "IDoc status 51"  # same text
has_connector_data = True  # connector real reportou error_code = "IDOC_STATUS_51"
```

Qual `evidence_strength` em cada caso?

**Solução:**
- **Caso 1:** `evidence_strength = 0.70` (texto puro, mais frágil)
- **Caso 2:** `evidence_strength = 0.95` (conector real, observação direta)

---

## 6. Invariantes Críticas

1. **DA-33:** Rule Engine avaliado **ANTES** de LLM — se casar, `llm_provider_used = "rule_engine"`, `prompt_digest = None`
2. **Regex com `re.IGNORECASE`** — maiúsculas/minúsculas não importam
3. **`confidence` fixo em `0.90`** — alta certeza por pattern matching
4. **`evidence_strength` binária:** `0.95` (connector real) ou `0.70` (só texto)
5. **`match_known_error()` NUNCA chama LLM** — é puramente determinístico

---

## 7. Limitações Conhecidas

1. **Regex estático** — não aprende com novos padrões (futuro: DA-33 v2 com ML)
2. **Sem fuzzy matching** — require pattern exato ou regex bem configurado
3. **Sem contexto multi-passos** — cada regra avaliada isoladamente (ex: "se erro X, verificar Y, se falhar, fazer Z" exigiria estado entre chamadas)

---

## 8. Referências Rápidas

### 8.1. Arquivos-Chave

| Arquivo | Responsabilidade | DAs |
|---------|-----------------|-----|
| `app/agent/rules.py` | Rule Engine determinístico (21+ regras regex) | DA-33 |
| `app/agent/nodes.py` | `_run_diagnosis_agent()` invoca `match_known_error()` | DA-22/33/53/59 |
| `app/agent/state.py` | `CopilotState`, `DiagnosisModel` (schema de saída) | DA-22/53/55 |

### 8.2. Invariantes na Prática

| Invariante | Onde verificar | Exemplo de erro |
|------------|----------------|-----------------|
| Rule engine before LLM | `nodes.py::_run_diagnosis_agent()` (l.925-935) | LLM chamado antes de `match_known_error()` |
| `llm_provider_used = "rule_engine"` | `rules.py::match_known_error()` | LLM chamado mesmo com regra casada |
| `prompt_digest = None` | `nodes.py` (return early após rule match) | `prompts.get_spec().provenance()` chamado para rule engine |

### 8.3. Decisões de Arquitetura

| DA | O que é | Onde |
|----|---------|------|
| DA-33 | Rule Engine determinístico (21+ regras, antes de LLM) | `rules.py`, `nodes.py` |
| DA-22 | Multi-agent: roteamento determinístico SAP/SAAS/Generic | `supervisor.py` |
| DA-53 | Prompt versionado com `digest` SHA-256 | `prompts.py`, `nodes.py` |

---

**Próximos passos:** Continue para `DA_AULA_12_ESCALATION.md` para profundidade no signal determinístico de escalonamento (DA-44).
