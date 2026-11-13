"""Regressao offline do DF-e Copilot sobre o dataset sintetico v1 (P7).

Compara as ferramentas deterministicas com o gabarito de cada caso. O
gabarito so vale como referencia fiscal para os casos `reviewed=true`
(data/eval/revisao_v1.yaml); os demais medem consistencia com as regras.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from dfe_copilot.plugin import PLUGIN

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data" / "eval" / "cases_v1.jsonl"
CASES = [
    json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()
]


def test_dataset_atualizado_com_o_gerador() -> None:
    spec = importlib.util.spec_from_file_location(
        "gen_dataset", ROOT / "scripts" / "gen_dataset.py"
    )
    gen = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(gen)  # type: ignore[union-attr]
    assert gen.casos() == CASES, "rode: uv run python packages/dfe/scripts/gen_dataset.py"


def test_dataset_cobre_os_cenarios() -> None:
    cenarios = {c["scenario"].split("_")[0] for c in CASES}
    assert {"uc01", "uc02", "uc03", "uc04", "uc05", "uc06", "sec", "gap"} <= cenarios
    assert all(c["synthetic"] for c in CASES)


def test_dataset_v1_tem_100_casos_unicos_e_splits() -> None:
    ids = [c["case_id"] for c in CASES]
    assert len(ids) == 100 and len(set(ids)) == 100
    assert {c["split"] for c in CASES} == {"dev", "validation", "blind"}
    assert all(c["description"] and c["rationale"] for c in CASES)


def test_revisao_cobre_todos_os_casos() -> None:
    import yaml

    rev = yaml.safe_load((ROOT / "data" / "eval" / "revisao_v1.yaml").read_text()) or {}
    casos = rev.get("casos") or {}
    assert set(casos) == {c["case_id"] for c in CASES}
    assert {(v or {}).get("status") for v in casos.values()} <= {"pendente", "aprovado", "corrigir"}
    for c in CASES:
        assert c["reviewed"] == ((casos[c["case_id"]] or {}).get("status") == "aprovado")


def test_folha_de_revisao_atualizada() -> None:
    spec = importlib.util.spec_from_file_location(
        "review_sheet", ROOT / "scripts" / "review_sheet.py"
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert (ROOT / "docs" / "REVISAO_DATASET.md").read_text(encoding="utf-8") == mod.render(), (
        "rode: uv run python packages/dfe/scripts/review_sheet.py"
    )


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_ferramentas_batem_com_o_gabarito(case: dict) -> None:
    ctx = PLUGIN.validate_context(case["domain_context"])
    findings = PLUGIN.run({"payload": case["payload"], "domain_context": ctx})
    assert [f["id"] for f in findings] == case["expected_finding_ids"]
    assert not any(f["kind"] == "fact" and f["source"] == "llm" for f in findings)
