"""Plugin de dominio DF-e para o core (DA-94).

Carregado pelo core via `DOMAIN_PLUGINS=dfe_copilot.plugin`. So produz
findings deterministicos: fatos do XML (dfe_copilot.fiscal.xml_tools) e
hipoteses de regra (dfe_copilot.fiscal.correlation). Nunca chama LLM nem
sistema externo.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from dfe_copilot.context import FiscalContext
from dfe_copilot.fiscal import (
    FiscalCorrelationError,
    FiscalXmlError,
    SchemaPackage,
    analyze_fiscal_xml,
    diagnose_duplicity,
    diagnose_key_divergence,
    diagnose_timeout,
    normalize_events,
)
from dfe_copilot.prompt import DFE_PROMPT

_logger = logging.getLogger(__name__)

# (cstat) -> trechos da base fiscal; None = base desligada/indisponivel.
KbLookup = Callable[[str], list[dict[str, Any]]]


def _default_kb_lookup() -> KbLookup | None:
    """Base fiscal (dfe_kb) so com DFE_KB_ENABLED=true: CI e testes nao
    dependem de Qdrant; o ponto de entrada do produto liga por padrao."""
    if os.environ.get("DFE_KB_ENABLED", "").lower() != "true":
        return None

    def lookup(cstat: str) -> list[dict[str, Any]]:
        from dfe_copilot import kb

        client, dense_fn, sparse_fn = kb._core_backends()
        return kb.search(
            client, f"rejeicao cStat {cstat}", dense_fn, sparse_fn, cstat=cstat, top_k=3
        )

    return lookup


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
    prompt = DFE_PROMPT  # P8: persona fiscal + bloco de achados no LLM

    def __init__(self, kb_lookup: KbLookup | None | str = "default") -> None:
        self._kb_lookup = kb_lookup

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
                        _gap("F-amb", "XML de homologacao (tpAmb=2) num contexto de producao.")
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
            elif facts["document_cStat"] == "539":
                findings.extend(diagnose_key_divergence(facts, events)["findings"])
            findings.extend(self._rule_evidence(facts["document_cStat"]))
        elif events:
            refs = {e.document_ref for e in events}
            key = facts.get("access_key") or (refs.pop() if len(refs) == 1 else None)
            if key:
                findings.extend(diagnose_timeout(key, events)["findings"])
            else:
                findings.append(_gap("G-ref", "Eventos de mais de um documento sem chave no XML."))
        return _dedupe(findings)

    def _rule_evidence(self, cstat: str) -> list[dict[str, Any]]:
        """Trechos oficiais/curados da base fiscal para o cStat (P3).

        Base desligada: nada (o diagnostico segue so com as regras locais).
        Base ligada mas indisponivel ou sem trecho: lacuna explicita.
        """
        lookup = _default_kb_lookup() if self._kb_lookup == "default" else self._kb_lookup
        if lookup is None:
            return []
        try:
            hits = lookup(cstat)
        except Exception as exc:  # noqa: BLE001 - base indisponivel nao derruba o diagnostico
            _logger.warning("dfe plugin: base fiscal indisponivel: %s", exc)
            return [_gap("G-kb", "Base fiscal indisponivel; regra do cStat nao recuperada.")]
        if not hits:
            return [_gap("G-kb", f"Nenhum trecho aplicavel na base fiscal para cStat {cstat}.")]
        return [
            {
                "id": "F-kb",
                "kind": "fact",
                "source": "rule_reference",
                "statement": f"Base fiscal: {len(hits)} trecho(s) aplicavel(is) ao cStat {cstat}.",
                "evidence_refs": [h["evidence_id"] for h in hits],
                "rule_ref": next((r for h in hits for r in h.get("rule_ids", [])), None),
            }
        ]


def _dedupe(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for f in findings:
        if f["id"] not in seen:
            seen.add(f["id"])
            out.append(f)
    return out


PLUGIN = DfePlugin()
