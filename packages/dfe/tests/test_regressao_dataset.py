"""T11: regressao offline do DF-e Copilot sobre o dataset sintetico v0.

Compara as ferramentas deterministicas com o gabarito de cada caso. O
gabarito v0 nao e' revisado por especialista fiscal (PENDENCIAS P7).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from dfe_copilot.plugin import PLUGIN

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data" / "eval" / "cases_v0.jsonl"
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
    assert {"uc01", "uc02", "uc03", "sec", "gap"} <= cenarios
    assert all(c["synthetic"] for c in CASES)


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_ferramentas_batem_com_o_gabarito(case: dict) -> None:
    ctx = PLUGIN.validate_context(case["domain_context"])
    findings = PLUGIN.run({"payload": case["payload"], "domain_context": ctx})
    assert [f["id"] for f in findings] == case["expected_finding_ids"]
    assert not any(f["kind"] == "fact" and f["source"] == "llm" for f in findings)
