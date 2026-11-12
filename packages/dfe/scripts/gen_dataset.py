"""Gera o dataset sintetico v0 do DF-e Copilot (T10, gabarito NAO revisado).

Cada caso traz entrada (payload, domain_context) e o gabarito esperado das
ferramentas deterministicas (`expected_finding_ids`). O gabarito e' derivado
das regras implementadas; a revisao fiscal humana (P7) e' pendente.

Uso: uv run python packages/dfe/scripts/gen_dataset.py  (regrava data/eval/cases_v0.jsonl)
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "eval" / "cases_v0.jsonl"
POSITIVO = (ROOT / "tests" / "fixtures" / "nfe_controle_positivo_sp.xml").read_text(
    encoding="utf-8"
)
NS = "http://www.portalfiscal.inf.br/nfe"
CNPJ = "11222333000181"  # ficticio


def _dv(body: str) -> str:
    total, w = 0, 2
    for c in reversed(body):
        total += int(c) * w
        w = w + 1 if w < 9 else 2
    r = total % 11
    return "0" if r < 2 else str(11 - r)


def chave(n: int, cuf: str = "35") -> str:
    body = f"{cuf}2611{CNPJ}55001{n:09d}1{n:08d}"
    return body + _dv(body)


def prot(key: str, cstat: str, motivo: str, tp_amb: str = "2") -> str:
    return (
        f'<protNFe xmlns="{NS}" versao="4.00"><infProt><tpAmb>{tp_amb}</tpAmb><chNFe>{key}</chNFe>'
        f"<cStat>{cstat}</cStat><xMotivo>{motivo}</xMotivo></infProt></protNFe>"
    )


def ev(eid: str, key: str, at: str, kind: str) -> dict:
    return {"evidence_id": eid, "document_ref": key, "at": at, "event": kind}


def casos() -> list[dict]:
    out: list[dict] = []

    def add(
        cid: str,
        scenario: str,
        payload: str | None,
        events: list,
        expected: list[str],
        env="homologation",
    ):
        out.append({
            "case_id": cid, "scenario": scenario, "synthetic": True, "reviewed": False,
            "payload": payload,
            "domain_context": {"environment": env, "jurisdiction": "SP", "events": events},
            "expected_finding_ids": expected,
        })  # fmt: skip

    for i in range(1, 6):  # UC01: 204 com reenvio apos timeout
        k = chave(100 + i)
        add(f"UC01-H1-{i:02d}", "uc01_204_reenvio", prot(k, "204", "Rejeicao: Duplicidade de NF-e"),
            [ev("A", k, f"2026-11-11T12:{50 + i}:00Z", "sent"), ev("T", k, f"2026-11-11T12:{50 + i}:30Z", "timeout"),
             ev("B", k, f"2026-11-11T12:{50 + i}:31Z", "sent")], ["F-cStat", "F-204", "H1"])  # fmt: skip
    for i in range(1, 4):  # UC01 controles negativos
        k = chave(200 + i)
        p = prot(k, "204", "Rejeicao: Duplicidade de NF-e")
        add(f"UC01-SEMTO-{i:02d}", "uc01_204_sem_timeout", p,
            [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("B", k, "2026-11-11T12:00:05Z", "sent")],
            ["F-cStat", "F-204", "G-204"])  # fmt: skip
        add(f"UC01-TARDE-{i:02d}", "uc01_204_timeout_tardio", p,
            [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("B", k, "2026-11-11T12:00:05Z", "sent"),
             ev("T", k, "2026-11-11T12:10:00Z", "timeout")], ["F-cStat", "F-204", "G-204"])  # fmt: skip
        k2 = chave(300 + i)
        add(f"UC01-DOCS-{i:02d}", "uc01_204_documentos_distintos", p,
            [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("T", k, "2026-11-11T12:00:30Z", "timeout"),
             ev("B", k2, "2026-11-11T12:00:31Z", "sent")], ["F-cStat", "F-204", "G-204"])  # fmt: skip
    for i in range(1, 4):  # UC02: timeout sem retorno / com resposta
        k = chave(400 + i)
        add(f"UC02-UNK-{i:02d}", "uc02_timeout_sem_retorno", None,
            [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("T", k, "2026-11-11T12:01:00Z", "timeout")],
            ["G-noxml", "F-timeout", "H-timeout"])  # fmt: skip
        add(f"UC02-RESP-{i:02d}", "uc02_timeout_com_resposta", None,
            [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("T", k, "2026-11-11T12:01:00Z", "timeout"),
             ev("R", k, "2026-11-11T12:01:20Z", "response")], ["G-noxml", "F-timeout"])  # fmt: skip
    # UC03: XSD
    add("UC03-VALIDO-01", "uc03_controle_positivo", POSITIVO, [], [])
    for i, (alvo, novo) in enumerate(
        [(f"<CNPJ>{CNPJ}</CNPJ>", ""), ("<natOp>", "<natOpX>"), ("<mod>55</mod>", "<mod>99</mod>")],
        start=1,
    ):
        mut = POSITIVO.replace(alvo, novo, 1)
        if alvo == "<natOp>":
            mut = mut.replace("</natOp>", "</natOpX>", 1)
        add(f"UC03-MUT-{i:02d}", "uc03_mutacao_xsd", mut, [], ["F-xsd"])
    add("UC03-AMB-01", "uc03_homologacao_em_producao", POSITIVO, [], ["G-amb"], env="production")
    # Seguranca / lacunas
    add(
        "SEC-XXE-01",
        "sec_xxe",
        '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',
        [],
        ["G-xml"],
    )
    add("SEC-MAL-01", "sec_malformado", "<protNFe>", [], ["G-xml"])
    add("GAP-DV-01", "gap_dv_invalido", prot(chave(500)[:43] + "0" if chave(500)[43] != "0" else chave(500)[:43] + "1",
        "204", "Rejeicao: Duplicidade de NF-e"), [], ["F-cStat", "F-dv"])  # fmt: skip
    add("GAP-TZ-01", "gap_evento_sem_fuso", None,
        [{"evidence_id": "A", "document_ref": chave(600), "at": "2026-11-11T12:00:00", "event": "sent"}],
        ["G-noxml", "G-events"])  # fmt: skip
    return out


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows = casos()
    OUT.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    print(f"{len(rows)} casos -> {OUT}")


if __name__ == "__main__":
    main()
