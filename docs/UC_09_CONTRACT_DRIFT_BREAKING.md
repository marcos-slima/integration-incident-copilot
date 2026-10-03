# Use Case 9: Contract Drift Breaking (DA-52)

**Contexto:** SAP republica endpoint OData com propriedade removida → breaking change detected

## Fluxo (DA-52: Contract Drift Detection)

### 1. Probe (app/connectors/odata_connector.py::fetch_contract)

```python
# app/connectors/odata_connector.py:fetch_contract()
def fetch_contract(self) -> Contract:
    # 1. Fetch $metadata
    url = f"{self.base_url}/$metadata"
    response = self._get(url)
    edmxml = response.text

    # 2. Parse EDMX
    contract = parse_edmx(edmxml)  # app/contracts/odata.py

    # 3. Generate fingerprint
    fingerprint = contract.fingerprint()  # Properties ordenadas, namespace fora

    return Contract(
        system_key=self.system_key,
        interface_type="odata",
        contract=contract,
        fingerprint=fingerprint,
        fetched_at=datetime.utcnow()
    )
```

### 2. Parse EDMX (app/contracts/odata.py::parse_edmx)

```python
def parse_edmx(xml: str) -> Contract:
    # Parse XML (lxml or xml.etree)
    tree = ET.fromstring(xml)

    # Extract Entities, Properties, Annotations
    entities = []
    for entity_type in tree.findall(".//{http://docs.oasis-open.org/odata/ns/edm}EntityType"):
        properties = []
        for prop in entity_type.findall(".//{http://docs.oasis-open.org/odata/ns/edm}Property"):
            properties.append({
                "name": prop.get("Name"),
                "type": prop.get("Type"),
                "nullable": prop.get("Nullable", "true") == "true"
            })
        entities.append({
            "name": entity_type.get("Name"),
            "properties": tuple(sorted(properties, key=lambda p: p["name"]))  # Ordenação! DA-52
        })

    # Generate fingerprint
    fingerprint = compute_fingerprint(entities)

    return Contract(entities=tuple(entities), fingerprint=fingerprint)
```

### 3. Compute Fingerprint (app/contracts/model.py)

```python
# DA-52: fingerprint NEVER raw XML — Properties ordenadas, namespace fora
def compute_fingerprint(entities: tuple) -> str:
    # Normalize: sort properties, remove namespace/version
    normalized = []
    for entity in entities:
        normalized.append({
            "name": entity["name"],
            "properties": entity["properties"]  # tuple ordenado
        })

    # Hash (SHA-256)
    import hashlib
    data = json.dumps(normalized, sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()[:16]
```

**Exemplo:**
```python
entities = [
    {"name": "Product", "properties": (
        {"name": "ProductID", "type": "Edm.String", "nullable": False},
        {"name": "Name", "type": "Edm.String", "nullable": True}
    )}
]

fingerprint = compute_fingerprint(entities)
# "a1b2c3d4e5f67890"
```

### 4. Baseline Query (app/contracts/baseline.py)

```python
# app/contracts/baseline.py::SystemContract ORM
class SystemContract(Base):
    __tablename__ = "system_contracts"

    id = Column(Integer, primary_key=True)
    system_key = Column(String, nullable=False)  # FK não (append-only)
    interface_type = Column(String, nullable=False)
    fingerprint = Column(String, nullable=False)
    fetched_at = Column(DateTime, nullable=False)
```

**Insert (append-only, sem FK):**
```sql
INSERT INTO system_contracts (system_key, interface_type, fingerprint, fetched_at)
VALUES ('SAP-SD', 'odata', 'a1b2c3d4e5f67890', '2026-10-01T10:00:00Z')
-- NUNCA UPDATE ou DELETE
```

### 5. Diff Detection (app/contracts/diff.py)

```python
# app/contracts/diff.py::detect_drift()
def detect_drift(old_entities: tuple, new_entities: tuple) -> DriftResult:
    # Compare entities by name
    old_map = {e["name"]: e for e in old_entities}
    new_map = {e["name"]: e for e in new_entities}

    breaking = []
    additive = []
    cosmetic = []

    for name, new_entity in new_map.items():
        if name not in old_map:
            additive.append(f"New entity: {name}")
        else:
            old_entity = old_map[name]
            changes = _compare_properties(old_entity["properties"], new_entity["properties"])
            for change in changes:
                if change["type"] == "removed":
                    breaking.append(f"Property removed: {name}.{change['name']}")
                elif change["type"] == "renamed":
                    cosmetic.append(f"Property renamed: {name}.{change['old']} → {change['new']}")
                else:
                    additive.append(f"Property modified: {name}.{change['name']}")

    return DriftResult(breaking=breaking, additive=additive, cosmetic=cosmetic)
```

**Exemplo de drift breaking:**
```python
# Old:
[{"name": "Product", "properties": (
    {"name": "ProductID", ...},
    {"name": "Name", ...},
    {"name": "Price", ...}  # ← REMOVIDO
)]}

# New:
[{"name": "Product", "properties": (
    {"name": "ProductID", ...},
    {"name": "Name", ...}
)]}

# Result:
drift = detect_drift(old, new)
drift.breaking  # ["Property removed: Product.Price"]
drift.additive  # []
drift.cosmetic  # []
```

### 6. Observer (app/contracts/observe.py)

```python
# app/contracts/observe.py::contract_drift_event()
def contract_drift_event(drift: DriftResult, contract: Contract):
    if not drift.breaking:
        return  # Only breaking opens incident

    # Create incident
    incident = Incident(
        description=f"OData contract breaking change: {', '.join(drift.breaking)}",
        interface_type="odata",
        identifier=contract.system_key,
        # ...
    )

    # Send CloudEvent (DA-23)
    cloudevent = CloudEvent(
        type="com.sap.iic.contract.drift",
        source=contract.base_url,
        data={
            "system_key": contract.system_key,
            "breaking_changes": drift.breaking,
            "previous_fingerprint": contract.previous_fingerprint,
            "new_fingerprint": contract.fingerprint
        }
    )

    # Event Mesh (Solace)
    send_to_event_mesh(cloudevent)
```

## Severidade (DA-52)

| Tipo | Exemplo | Abre incidente? |
|---|---|---|
| **breaking** | Property removed, type changed | ✅ Sim |
| **additive** | New property added | ❌ Não |
| **cosmetic** | Property renamed (renaming) | ❌ Não |

**Rationale:**
- Breaking: clients quebram
- Additive: backward compatível
- Cosmetic: renamed é *cosmético* se alias mantido (DA-52)

## DA-52: Armadilhas Evitadas

1. **Fingerprint nunca bruto XML** →Properties ordenadas, namespace ignored
2. **Baseline append-only** → Sem FK, histórico completo
3. **Só breaking abre incidente** → additive/cosmetic só logging
4. **Fingerprints persistentes** → Comparação entre versões

## Exemplo Completo

### Step 1: First Observation
```python
# fetch_contract()第一次
contract = odata_connector.fetch_contract()
# fingerprint = "abc123"
# baseline: Nenhum (primeira vez)

# Record first_observation
baseline.insert(
    system_key="SAP-SD",
    fingerprint="abc123",
    fetched_at=datetime.utcnow()
)
```

### Step 2: Next Observation
```python
# fetch_contract() segunda
contract = odata_connector.fetch_contract()
# fingerprint = "def456" (mudou!)

# Query baseline
baseline = get_baseline("SAP-SD")
# baseline.fingerprint = "abc123"

# Diff
drift = detect_drift(baseline.entities, contract.entities)
# drift.breaking = ["Property removed: Product.Price"]

# Open incident
if drift.breaking:
    record_incident(drift)
    send_cloudevent(drift)
```

## Database Schema (Migration 005)

```sql
CREATE TABLE system_contracts (
    id SERIAL PRIMARY KEY,
    system_key VARCHAR(255) NOT NULL,
    interface_type VARCHAR(50) NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index para lookup
CREATE INDEX idx_system_contracts_system_key ON system_contracts(system_key);

-- Append-only: NENHUM UPDATE/DELETE (verificado por trigger)
CREATE OR REPLACE FUNCTION prevent_contract_update()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'system_contracts is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_prevent_contract_update
BEFORE UPDATE OR DELETE ON system_contracts
FOR EACH ROW EXECUTE FUNCTION prevent_contract_update();
```
