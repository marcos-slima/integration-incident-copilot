# Use Case 8: GraphRAG Enabled (Neo4j)

**Contexto:** `USE_GRAPH_RAG=true` → enriquecimento de contexto via Neo4j (GraphRAG)

## Grapho Completo

```
supervisor → connector → retrieve → graph_enrich → {sap|saas|generic}_diagnose → graph_write → report
```

## Nodes (app/agent/nodes.py)

### 1. `graph_enrich_node` (163–196)

```python
# app/agent/nodes.py:163–196
def graph_enrich_node(state: CopilotState) -> CopilotState:
    """Consultas Cypher em Neo4j para histórico semelhante."""
    graph_context = get_graph_context(state)
    return {"graph_context": graph_context}
```

**Query Cypher (app/rag/graph_store.py):**

```cypher
MATCH (i:Incident {identifier: $identifier, interface_type: $interface_type})
RETURN i.description AS description, i.probable_root_cause AS root_cause
```

**Exemplo de resultado:**
```json
{
  "identifier": "TICKET-123",
  "interface_type": "servicenow",
  "graph_context": [
    {
      "description": "HTTP 401 ao chamar incident API",
      "root_cause": "OAuth2 token expired"
    },
    {
      "description": "Authentication failed on ServiceNow incident retrieval",
      "root_cause": "Invalid Credentials"
    }
  ]
}
```

### 2. `graph_write_node` (198–211)

```python
# app/agent/nodes.py:198–211
def graph_write_node(state: CopilotState) -> CopilotState:
    """Grava diagnostico em Neo4j após conclusão."""
    if not state.get("graph_context"):
        return state  # Skip se GraphRAG não disponível

    upsert_incident_graph(state)
    return state
```

**Cypher (upsert):**
```cypher
MERGE (i:Incident {identifier: $identifier, interface_type: $interface_type})
MERGE (r:Resolution {root_cause: $root_cause})
MERGE (i)-[:HAS_RESOLUTION]->(r)
```

**Exemplo de gravação:**
```json
{
  "identifier": "TICKET-123",
  "interface_type": "servicenow",
  "root_cause": "OAuth2 token expired",
  "timestamp": "2026-10-02T10:15:30Z"
}
```

## DA-21: Handling Neo4j Errors

```python
# app/rag/graph_store.py
from neo4j import DriverError, TransientError, Neo4jError

def upsert_incident_graph(state: CopilotState):
    try:
        driver = get_driver()
        driver.execute_query(cypher, **params)
    except (DriverError, TransientError):
        # Graceful: log and continue (não quebra o diagnóstico)
        logger.warning("Neo4j transient error, continuing without graph write")
    except Neo4jError:
        logger.error("Neo4j permanent error", exc_info=True)
```

**Comportamento:** Se Neo4j indisponível → gravação falha, mas diagnóstico continua normalmente

## Performance

**Latência adicional:** ~200ms (query Cypher + serialization)
**Conexão pool:** Reutilização de driver (single instance por processo)

## DA-28: Endpoint `/incidents/{id}/verify`

```python
# app/rag/graph_store.py
def verify_incident_graph(incident_id: str):
    query = """
    MATCH (i:Incident {identifier: $incident_id})
    OPTIONAL MATCH (i)-[:HAS_RESOLUTION]->(r:Resolution)
    RETURN i.verified AS verified, r.root_cause AS root_cause
    """
    result = execute_query(query, incident_id=incident_id)
    return result.single()
```

**Resposta:**
```json
{
  "verified": true,
  "root_cause": "OAuth2 token expired"
}
```

## Limitações

1. **Opt-in:** Desligado por default (`USE_GRAPH_RAG=false`)
2. **Best-effort:** Falha não quebra diagnóstico
3. **Schema fixo:** `Incident` + `Resolution` + `HAS_RESOLUTION`
4. **Sem FK:** Desacoplado de PostgreSQL (data warehouse vs operational NOSQL)

## Exemplo de Workflow

### Com GraphRAG:
1. `POST /diagnose`
2. `graph_enrich_node` → busca histórico no Neo4j
3. `sap_diagnosis_node` → prompt inclui `graph_context`
4. LLM usa histórico para inferir solução
5. `graph_write_node` → grava novo incidente + resolução

### Sem GraphRAG (default):
1. `POST /diagnose`
2. `graph_enrich_node` → detecta `graph_context=None`, skip
3. `sap_diagnosis_node` → prompt sem contexto histórico
4. LLM relying only RAG (Qdrant)

## Comparação: Qdrant vs Neo4j

| Aspecto | Qdrant (RAG default) | Neo4j (GraphRAG opt-in) |
|---|---|---|
| Tipo | Vector database | Graph database |
| Query | Vector similarity + hybrid (dense+sparse) | Cypher path queries |
| Contexto | Top-k documentos similares | Histórico de incidentes relacionados |
| Latência | ~100ms | ~200ms |
| Persistence | Qdrant storage | Neo4j store |
| Fallback | reference_library | Nenhum (GraphRAG é opt-in) |

## Schema GraphRAG (DA-28)

```cypher
// Vertices
CREATE CONSTRAINT incident_identifier IF NOT EXISTS FOR (i:Incident) REQUIRE i.identifier IS UNIQUE
CREATE CONSTRAINT resolution_root_cause IF NOT EXISTS FOR (r:Resolution) REQUIRE r.root_cause IS UNIQUE

// Edges
// i:Incident -[:HAS_RESOLUTION]-> r:Resolution
```

**Exemplo de query full graph:**
```cypher
MATCH (i:Incident)-[:HAS_RESOLUTION]->(r:Resolution)
WHERE i.interface_type = $interface_type
RETURN i.identifier, r.root_cause
LIMIT 10
```
