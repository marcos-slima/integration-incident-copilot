"""DA-50: correlaciona incidentes diagnosticados com os sistemas
integrados do catalogo (integration_systems, DA-49).

O problema que resolve
---------------------
`incidents.connector_source_system` e uma String(128) livre, e ate a
DA-50 era preenchida com o ROTULO do conector ("OData", "SAP CAP",
"SAP API Management" - hardcoded em cada ConnectorResult.source_system),
enquanto o catalogo usa `system_key` ("cap_prod", "sap_apim"). Sem uma
ponte, o operador nao conseguia responder "quais sistemas estao
gerando incidente?" nem "este incidente veio de qual sistema?".

Regras de correlacao (deterministicas, em ordem de prioridade)
--------------------------------------------------------------
1. `connector_source_system` casa exatamente com um `system_key`
   cadastrado -> match exato (o cliente informou qual sistema e).
2. Sem match exato: `interface_type` == `connector_type` do catalogo
   -> match por conector:
   - exatamente 1 sistema com aquele connector_type -> resolve;
   - mais de 1 (mesmo conector em ambientes/vendors diferentes,
     ex: cap_prod e cap_stage) -> AMBIGUO: nao escolhe
     arbitrariamente, devolve os candidatos para o operador decidir
     (mesmo principio fail-closed da Capability Registry, DA-27);
   - nenhum -> sem match.
3. `interface_type` ausente (diagnostico sem conector) -> so vale a
   regra 1.

Tudo aqui e funcao PURA (entrada -> dict), sem sessao e sem I/O: a
correlacao e testavel sem banco e determinismo e o requisito (a
superfície admin nunca pode "adivinhar" um sistema).
"""

from __future__ import annotations

from typing import Any

MATCH_SYSTEM_KEY = "system_key"
MATCH_CONNECTOR_TYPE = "connector_type"
MATCH_AMBIGUOUS = "ambiguous"
MATCH_NONE = "none"


def build_system_index(systems: list[Any]) -> dict[str, Any]:
    """Indice de correlacao a partir de IntegrationSystem (ou dicts com as
    mesmas chaves, para teste). Chaves: 'by_key' (system_key -> sistema) e
    'by_connector_type' (connector_type -> [sistemas])."""
    by_key: dict[str, Any] = {}
    by_connector_type: dict[str, list[Any]] = {}
    for system in systems:
        key = _attr(system, "system_key")
        if key:
            by_key[key] = system
        connector_type = _attr(system, "connector_type")
        if connector_type:
            by_connector_type.setdefault(connector_type, []).append(system)
    return {"by_key": by_key, "by_connector_type": by_connector_type}


def correlate(
    interface_type: str | None,
    connector_source_system: str | None,
    index: dict[str, Any],
) -> dict[str, Any]:
    """Resolve o sistema integrado de um incidente. Retorna dict serializavel:

    {
      "match": "system_key" | "connector_type" | "ambiguous" | "none",
      "system_key": str | None, "name": str | None, "vendor": str | None,
      "status": str | None, "environment": str | None, "connector_type": str | None,
      "candidates": [system_key, ...]   # so quando ambíguo
    }
    """
    by_key: dict[str, Any] = index.get("by_key", {})
    by_connector_type: dict[str, list[Any]] = index.get("by_connector_type", {})

    source = (connector_source_system or "").strip()
    if source and source in by_key:
        return _match(MATCH_SYSTEM_KEY, by_key[source])

    candidates = by_connector_type.get(interface_type or "", [])
    if len(candidates) == 1:
        return _match(MATCH_CONNECTOR_TYPE, candidates[0])
    if len(candidates) > 1:
        return {
            "match": MATCH_AMBIGUOUS,
            "system_key": None,
            "name": None,
            "vendor": None,
            "status": None,
            "environment": None,
            "connector_type": interface_type,
            "candidates": sorted(_attr(c, "system_key") or "" for c in candidates),
        }
    return _match(MATCH_NONE, None)


def _match(kind: str, system: Any | None) -> dict[str, Any]:
    return {
        "match": kind,
        "system_key": _attr(system, "system_key"),
        "name": _attr(system, "name"),
        "vendor": _attr(system, "vendor"),
        "status": _attr(system, "status"),
        "environment": _attr(system, "environment"),
        "connector_type": _attr(system, "connector_type"),
        "candidates": [],
    }


def _attr(obj: Any, name: str) -> str | None:
    value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    return value if value is None else str(value)
