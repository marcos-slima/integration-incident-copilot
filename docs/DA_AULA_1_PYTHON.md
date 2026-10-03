# Aula 1 — Python para quem já programa (Módulo 1)

## Objetivo

Usar o código real do projeto como fonte de verdade para consolidar os fundamentos de Python que você já domina: type hints, funções, módulos, imports e `None`. Começamos com `app/models.py` e `app/config.py`.

## Preparação

```bash
# Ativar ambiente (qualquer shell)
PATH="$PWD/.venv/bin:$PATH"

# Ver o que está instalado (sem instalar nada novo)
uv pip list | grep -E 'pydantic|fastapi'
```

Você já sabe Python? Sim → pule para "O campo `description` em `IncidentRequest`".

Você está estudando Python agora? Não se preocupe: o objetivo é **aplicar o que você conhece** a um código real, não aprender do zero.

---

## O campo `description` em `IncidentRequest`

### Leitura base

```python
# app/models.py:51-58
class IncidentRequest(BaseModel):
    """Requisição de diagnóstico.

    Contrato de entrada do endpoint POST /diagnose.
    """

    description: str = Field(
        description=(
            "Descrição libre-texto do incidente. Exemplos: "
            "'IDoc 51 no processo de pedido de compra', "
            "'HTTP 500 ao acessar OData do SAP'..."
        )
    )
    logs: str | None = Field(
        default=None,
        description="Logs de erro completos.",
    )
    payload: str | None = Field(
        default=None,
        description="Payload em texto plano.",
    )
```

### Pergunta fundamental

Você consegue distinguir **três camadas** no campo `description`?

1. Type hint (`str`)
2. Valor default (não há)
3. Validação (não há restrição explícita além do tipo)

### Resposta rápida

| Camada | O que é | Exemplo em `description` |
|---|---|---|
| **Type hint** | Anotação de tipo (documentação + check estático) | `str` |
| **Valor default** | Valor se não informado | *não há (requisito)* |
| **Validação** | Regras adicionais no construtor | *não há restrição explícita* |

---

## Type hints em Python

### Anotações são opcionalmente aplicadas em tempo de execução

```python
# Exemplo simples (não do projeto)
def suma(a: int, b: int) -> int:
    return a + b


# Em tempo de execução, type hints NÃO são forçadas:
suma(2, 3)  # funciona
suma("a", "b")  # também funciona — str + str = concatenação!
```

Type hints são:
- **Documentação declarativa**: "expected input/output"
- **Check estático**: ferramentas como `ruff` ou `mypy` validam
- **Runtime opcional**: Python não valida por default

### Mas então por que `BaseModel` é diferente?

```python
# app/models.py:51
class IncidentRequest(BaseModel):
    description: str
```

Pydantic **valida em tempo de construtor**. Exemplo:

```python
from pydantic import TypeAdapter

ta = TypeAdapter(IncidentRequest)

# Isso funciona:
ta.validate_python({"description": "IDoc 51"})

# Isso falha com ValidationError:
ta.validate_python({"description": 123})  # int ≠ str
```

### O que `BaseModel` faz?

| Comportamento | Sem `BaseModel` | Com `BaseModel` |
|---|---|---|
| `description=123` | Aceita (int) | `ValidationError` (int ≠ str) |
| `logs=None` | Aceita | Aceita (tipo `str \| None`) |
| `payload="texto"` | Aceita | Aceita (valida contra tipo) |

---

## `Field()` e metadados de schema

### Com `Field` no código real:

```python
# app/models.py:14-63
from pydantic import Field


class IncidentRequest(BaseModel):
    description: str = Field(max_length=5_000)
    logs: str | None = Field(default=None, max_length=50_000)
    payload: str | None = Field(default=None, max_length=50_000)
    interface_type: Literal["odata", "rfc", "servicenow", ...] | None = None
    identifier: str | None = None
    connector_source_system: str | None = Field(default=None, description="...")
    sensitivity_level: Literal["public", "internal", "confidential", "secret"] | None = None
    pii_detected: bool | None = None
    redaction_applied: bool | None = None
```

### Diferença prática

| Meta | Sem `Field()` | Com `Field()` |
|---|---|---|
| Valor default | `None` (implícito) | explícito (`default=None`) |
| Documentação no schema | *não há* | `Field(..., description=...)` |
| Validação extra | *nenhuma* | `ge=0.0`, `max_length=5_000`, `Literal[...]` |

### Exemplo de validação `Literal`

```python
# app/models.py:18-32
    interface_type: (
        Literal[
            "odata",
            "rfc",
            "servicenow",
            "salesforce",
            "workday",
            "ariba",
            "successfactors",
            "po",
            "cap",
            "apim",
        ]
        | None
    ) = None
```

Aqui `Literal` restringe os valores possíveis a **apenas** os da lista. `interface_type="unexpected"` gera `ValidationError`.

### Exemplo de validação `max_length`

```python
# app/models.py:15-17
    description: str = Field(max_length=5_000)
    logs: str | None = Field(default=None, max_length=50_000)
    payload: str | None = Field(default=None, max_length=50_000)
```

`description="x" * 5_001` → `ValidationError` (5001 > 5000).

---

## Onde `description` viaja?

### Passageiro de estado

```python
# app/agent/state.py:59-64
class CopilotState(TypedDict, total=False):
    description: str  # ← mesmo nome, mesma semântica
    logs: str | None
    payload: str | None
    interface_type: str | None
    identifier: str | None
    connector_source_system: str | None
    ...
```

**O campo `description` passa de `IncidentRequest.description` para `CopilotState.description`**, mantendo o tipo `str` e o papel (texto livre do usuário sobre o incidente).

---

## `None` vs. ausência de valor

### Tipos Union com `None`

```python
# app/models.py:58-62
    logs: str | None = Field(
        default=None,
        description="Logs de erro completos.",
    )
```

`str \| None` = "pode ser `str` **ou** `None`"

| Expressão | Resultado |
|---|---|
| `IncidentRequest(description="...")` | `logs=None` (default) |
| `IncidentRequest(description="...", logs=None)` | `logs=None` (explícito) |
| `IncidentRequest(description="...", logs="error 404")` | `logs="error 404"` |

### `None` não é "sem valor" — é um valor

```python
# Exemplo simples
x: str | None = None  # x tem o valor None
y: str | None  # y não está definido (NameError se usar)
```

| Variável | Tem valor? | Tipo |
|---|---|---|
| `x = None` | Sim (o valor `None`) | `NoneType` |
| `y` (não definido) | Não | — |

---

## `app/config.py`: default e validação cruzada

### Exemplo: `llm_send_seed`

```python
# app/config.py:96
    llm_send_seed: bool | None = None
```

- **Type hint**: `bool \| None`
- **Valor default**: `None`
- **Sem validação extra**: aceita `True`, `False`, ou `None`

### Exemplo: `api_key`

```python
# app/config.py:310
    api_key: str = ""
```

- **Type hint**: `str`
- **Valor default**: `""` (string vazia)
- **Validação**: `if not settings.api_key` → gera chave efêmera no startup (DA-18)

### Exemplo: `llm_gateway_max_cost_usd`

```python
# app/config.py:104
    llm_gateway_max_cost_usd: float = 0.50
```

- **Type hint**: `float`
- **Valor default**: `0.50`

### Valor default vazio ≠ `None`

| Campo | Default | Semântica |
|---|---|---|
| `api_key: str = ""` | `""` | string vazia (chave não configurada) |
| `llm_send_seed: bool \| None = None` | `None` | não informado (consultar tabela de capabilities) |

---

---

## Modelos de saída (`DiagnosisResponse`, `Evidence`)

### `DiagnosisResponse`

```python
# app/models.py:163-300
class DiagnosisResponse(BaseModel):
    probable_root_cause: str
    model_confidence: float = Field(ge=0.0, le=1.0)
    diagnosis_confidence: float = Field(ge=0.0, le=1.0)
    next_steps: list[str]
    report_markdown: str
    matched_source: str | None = None
    evidence_strength: float | None = Field(ge=0.0, le=1.0)
    llm_provider_used: str | None = None
    agent_domain: str | None = None
    llm_model: str | None = None
    prompt_version: str | None = None
    prompt_digest: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    incident_id: str | None = None
    trace_id: str | None = None
```

**Novidades:**
- `list[str]` = lista de strings
- `list[Evidence]` = lista de instâncias da classe `Evidence`
- `Field(default_factory=list)` = cria nova lista vazia para cada instância (evita compartilhamento mutable)
- `field: str \| None` = campo opcional (aceita `None` ou string)

### `Evidence`

```python
# app/models.py:132-161
class Evidence(BaseModel):
    source_id: str
    source_type: Literal["connector", "rag", "graph", "web", "user", "rule_engine"]
    locator: str | None = None
    excerpt: str
    retrieval_score: float | None = None
    rerank_score: float | None = None
    trust_level: Literal[
        "system_observed", "retrieved_document", "web_untrusted", "user_reported", "simulated"
    ]
```

**Novidades:**
- `Literal` fechado em `source_type` e `trust_level`
- `float \| None` para pontuações opcionais (quando não aplicáveis)

### `VerifyIncidentRequest`

```python
# app/models.py:302-351
class VerifyIncidentRequest(BaseModel):
    root_cause: str
    verified_by: Literal["human", "system"] = "human"
    correct: bool | None = None
    trace_id: str | None = None
```

**Novidades:**
- `verified_by="human"` (default explícito)
- `correct: bool \| None` = feedback opcional

---

## Onde os campos viajam?

### Passageiro de estado

```python
# app/agent/state.py:59-64
class CopilotState(TypedDict, total=False):
    description: str
    logs: str | None
    payload: str | None
    interface_type: str | None
    identifier: str | None
    connector_source_system: str | None
    ...
```

**O campo `description` passa de `IncidentRequest.description` para `CopilotState.description`**, mantendo o mesmo tipo (`str`) e semântica (texto livre do usuário sobre o incidente).

---

## Recapitulação

### Três camadas do campo `description`

| Camada | O que é | Código |
|---|---|---|
| **Type hint** | `str` (anotação de tipo) | `description: str` |
| **Valor default** | *não há* (campo obrigatório) | — |
| **Validação** | `max_length=5_000` | `Field(max_length=...)` |

### O que Pydantic `BaseModel` adiciona

- **Validação em construtor**: `description=123` → `ValidationError` (int ≠ str)
- **Validação adicional**: `description="x" * 5001` → `ValidationError` (excede `max_length`)
- **Default implícito**: campos não inicializados recebem `None` (se `str \| None`) ou quebram (se `str`)
- **Metadados via `Field()`**: `description=...` injeta texto no schema OpenAPI

### E aí vai...

`description` de `IncidentRequest` (entrada do endpoint) → `CopilotState.description` (estado do grafo LangGraph) → `DiagnosisResponse` (saída), mantendo o mesmo tipo (`str`) e semântica (texto livre do usuário sobre o incidente).

---

## Próximo passo

Você leu a fonte de verdade do Módulo 1 (Python para quem já programas) e distinguish as três camadas dos modelos `IncidentRequest`, `DiagnosisResponse`, `Evidence` e `VerifyIncidentRequest`.

Antes de passar para o Módulo 2 (FastAPI e HTTP), há perguntas ou obscuridades sobre:

1. Type hints (`str` vs `str \| None` vs `list[T]`)?
2. O que `BaseModel` faz além do tipo?
3. A diferença entre `None` e ausência de valor?
4. O uso de `Literal` para enums fechados?
5. O que `Field(default_factory=list)` faz de especial?

Se tudo está claro, continue para **Módulo 2: FastAPI e HTTP** (próxima aula). Se não, faça a pergunta.

---

## Referências rápidas

- `app/models.py`:14-63 (`IncidentRequest`)
- `app/models.py`:163-300 (`DiagnosisResponse`)
- `app/models.py`:132-161 (`Evidence`)
- `app/models.py`:302-351 (`VerifyIncidentRequest`)
- `app/agent/state.py`:59-64 (`CopilotState.description`)
- `app/config.py`:310 (`api_key: str = ""`)
- `app/config.py`:96 (`llm_send_seed: bool \| None = None`)
- Pydantic v2 docs: [Pydantic Models](https://docs.pydantic.dev/latest/concepts/models/), [Fields](https://docs.pydantic.dev/latest/concepts/fields/)

**Nota**: este documento é um guia de leitura, não uma modificaçao do código-fonte. O código-fonte é a fonte única de verdade; este texto sintetiza e contextualiza.
