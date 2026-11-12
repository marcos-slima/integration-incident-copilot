#!/usr/bin/env python3
"""Provider do promptfoo do DF-e Copilot (P8): roda o pipeline real do core
com o plugin DF-e ligado, para um caso de data/eval/cases_v0.jsonl.

Prompt do promptfoo = case_id (ex.: "UC01-H1-01"). Saida = JSON com a
proveniencia do prompt e o texto do diagnostico, para os asserts.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT.parents[1])]
os.environ.setdefault("DOMAIN_PLUGINS", "dfe_copilot.plugin")
os.environ.setdefault("RULE_ENGINE_ENABLED", "false")  # mede o LLM, nao a regra

from app.agent.graph import run_diagnosis
from app.models import IncidentRequest

CASES = ROOT / "data" / "eval" / "cases_v0.jsonl"


def main() -> None:
    model = sys.argv[1]
    case_id = (sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read()).strip()
    case = next(json.loads(line) for line in CASES.read_text().splitlines() if case_id in line)
    request = IncidentRequest(
        description=f"Incidente NF-e (caso sintetico {case['case_id']}): rejeicao/erro na autorizacao",
        payload=case["payload"] or None,
        domain_context=case["domain_context"],
    )
    result = run_diagnosis(request, llm_model=model)
    print(
        json.dumps(
            {
                "model_used": model,
                "prompt_version": result.prompt_version,
                "prompt_digest": result.prompt_digest,
                "probable_root_cause": result.probable_root_cause,
                "next_steps": result.next_steps,
                "finding_ids": [f.id for f in result.findings],
                "cause_status": result.cause_status,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
