"""DA-93: montagem deterministica de `findings` (fato x hipotese).

Regra central: o LLM nunca produz `fact`. Fatos vem de ferramentas
deterministicas (app/fiscal), conector real ou regra; a causa proposta pelo
diagnostico entra sempre como `hypothesis`. `confirmed` nao e' produzido
aqui - so por revisao humana registrada.
"""

from __future__ import annotations

from typing import Any

_FACT_SOURCES = {"tool", "connector", "rule_reference"}


def _coerce(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    if out.get("kind") == "fact" and out.get("source") not in _FACT_SOURCES:
        out["kind"] = "hypothesis"  # fato sem fonte deterministica e' rebaixado
    return out


def build_findings(
    diagnosis: dict[str, Any], tool_findings: list[dict[str, Any]] | None = None
) -> tuple[list[dict[str, Any]], str]:
    """Devolve (findings, cause_status) a partir do diagnostico e das ferramentas."""
    findings = [_coerce(f) for f in (tool_findings or [])]
    cause = (diagnosis.get("probable_root_cause") or "").strip()
    if cause and cause != "N/A":
        from_rule = str(diagnosis.get("llm_provider_used", "")).startswith("rule_engine")
        findings.append(
            {
                "id": "H-diagnosis",
                "kind": "hypothesis",
                "source": "rule_reference" if from_rule else "llm",
                "statement": cause,
                "evidence_refs": [diagnosis["matched_source"]]
                if diagnosis.get("matched_source")
                else [],
                "confirmation_test": [],
                "alternatives": [],
            }
        )
    has_hypothesis = any(f["kind"] == "hypothesis" for f in findings)
    return findings, "hypothesis" if has_hypothesis else "inconclusive"
