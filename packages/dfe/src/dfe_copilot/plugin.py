"""Plugin de dominio DF-e para o core (DA-94).

Carregado pelo core via `DOMAIN_PLUGINS=dfe_copilot.plugin`. So produz
findings deterministicos: fatos do XML (dfe_copilot.fiscal.xml_tools) e
hipoteses de regra (dfe_copilot.fiscal.correlation). Nunca chama LLM nem
sistema externo.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from dfe_copilot.context import FiscalContext
from dfe_copilot.fiscal import (
    FiscalCorrelationError,
    FiscalXmlError,
    SchemaPackage,
    analyze_fiscal_xml,
    diagnose_duplicity,
    diagnose_timeout,
    normalize_events,
)


@lru_cache(maxsize=1)
def _package() -> SchemaPackage:
    return SchemaPackage.load()


def _gap(fid: str, statement: str) -> dict[str, Any]:
    return {
        "id": fid,
        "kind": "fact",
        "source": "tool",
        "statement": statement,
        "evidence_refs": [],
    }


class DfePlugin:
    name = "dfe"

    def validate_context(self, context: dict[str, Any]) -> dict[str, Any] | None:
        if context.get("family") not in (None, "nfe"):
            return None
        return FiscalContext(**context).model_dump(mode="json")

    def run(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        ctx = state.get("domain_context") or {}
        findings: list[dict[str, Any]] = []
        facts: dict[str, Any] = {}
        payload = state.get("payload")
        if payload:
            try:
                facts = analyze_fiscal_xml(payload.encode("utf-8"), _package())
                findings.extend(facts["findings"])
                if facts.get("tpAmb") == "2" and ctx.get("environment") == "production":
                    findings.append(
                        _gap("G-amb", "XML de homologacao (tpAmb=2) num contexto de producao.")
                    )
            except FiscalXmlError as exc:
                findings.append(_gap("G-xml", f"XML recusado antes da analise ({exc.code})."))
        else:
            findings.append(_gap("G-noxml", "Sem XML no incidente; validacao XSD nao executada."))

        try:
            events = normalize_events(ctx.get("events") or [])
        except FiscalCorrelationError as exc:
            findings.append(_gap("G-events", f"Linha do tempo recusada ({exc.code})."))
            events = []

        if facts.get("document_cStat"):
            # Ha retorno do autorizador: o caso e' de interpretacao do retorno,
            # nao de "timeout sem resposta" (o proprio XML e' a resposta).
            if facts["document_cStat"] == "204":
                findings.extend(diagnose_duplicity(facts, events)["findings"])
        elif events:
            refs = {e.document_ref for e in events}
            key = facts.get("access_key") or (refs.pop() if len(refs) == 1 else None)
            if key:
                findings.extend(diagnose_timeout(key, events)["findings"])
            else:
                findings.append(_gap("G-ref", "Eventos de mais de um documento sem chave no XML."))
        return _dedupe(findings)


def _dedupe(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for f in findings:
        if f["id"] not in seen:
            seen.add(f["id"])
            out.append(f)
    return out


PLUGIN = DfePlugin()
