"""Gera docs/REVISAO_DATASET.md (P7) a partir de data/eval/cases_v1.jsonl e
data/eval/revisao_v1.yaml. A folha e' so leitura: a decisao do revisor fica
no YAML.

    uv run python packages/dfe/scripts/review_sheet.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "data" / "eval" / "cases_v1.jsonl"
REVISAO = ROOT / "data" / "eval" / "revisao_v1.yaml"
OUT = ROOT / "docs" / "REVISAO_DATASET.md"

FAMILIAS = {
    "uc01": "UC01 — cStat 204 e reenvio após timeout",
    "uc02": "UC02 — timeout sem retorno",
    "uc03": "UC03 — XSD e ambiente",
    "uc04": "UC04 — outros cStat",
    "uc05": "UC05 — lote e nfeProc",
    "uc06": "UC06 — assinatura XMLDSig",
    "sec": "SEC — segurança da entrada",
    "gap": "GAP — lacunas de entrada",
}


def render() -> str:
    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line]
    rev = yaml.safe_load(REVISAO.read_text(encoding="utf-8")) or {}
    status = {k: (v or {}).get("status", "pendente") for k, v in (rev.get("casos") or {}).items()}
    cont = Counter(status.get(c["case_id"], "pendente") for c in cases)
    splits = Counter(c["split"] for c in cases)
    linhas = [
        "# Revisão fiscal do dataset v1 (P7)",
        "",
        "> Gerado por `scripts/review_sheet.py`. Não edite aqui: a decisão vai em",
        "> `data/eval/revisao_v1.yaml` (`aprovado` | `corrigir` + nota).",
        "",
        f"- Casos: **{len(cases)}** · sintéticos, CNPJ fictício 11.222.333/0001-81",
        f"- Split: dev {splits['dev']} · validation {splits['validation']} · blind {splits['blind']}",
        f"- Revisão: aprovado {cont['aprovado']} · corrigir {cont['corrigir']} · pendente {cont['pendente']}",
        f"- Revisor: {rev.get('revisor') or '—'} · data: {rev.get('data_revisao') or '—'}",
        "",
        "**O que conferir em cada linha:** a justificativa descreve corretamente a",
        "regra fiscal/técnica, e os achados esperados são os que um N2/N3 esperaria",
        "das ferramentas (fatos `F-*`, lacunas `G-*`, hipóteses `H*`). Casos `blind`",
        "não devem ser usados para ajustar regra nem prompt.",
        "",
    ]
    for fam, titulo in FAMILIAS.items():
        grupo = [c for c in cases if c["scenario"].split("_")[0] == fam]
        if not grupo:
            continue
        linhas += [
            f"## {titulo}",
            "",
            "| Caso | Split | Esperado | Justificativa | Status |",
            "|---|---|---|---|---|",
        ]
        for c in grupo:
            esperado = ", ".join(f"`{i}`" for i in c["expected_finding_ids"]) or "—"
            linhas.append(
                f"| {c['case_id']} | {c['split']} | {esperado} | {c['rationale']} | "
                f"{status.get(c['case_id'], 'pendente')} |"
            )
        linhas.append("")
    return "\n".join(linhas)


def main() -> None:
    OUT.write_text(render(), encoding="utf-8")
    print(f"folha: {OUT}")


if __name__ == "__main__":
    main()
