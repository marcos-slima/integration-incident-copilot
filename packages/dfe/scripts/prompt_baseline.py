"""Digest do prompt fiscal (P8) e baseline de medicao.

    uv run --project ../.. env PYTHONPATH=src:../.. python scripts/prompt_baseline.py          # mostra
    uv run --project ../.. env PYTHONPATH=src:../.. python scripts/prompt_baseline.py --write \
        --measurement "promptfoo AAAA-MM-DD: qwen3.6:35b-a3b, N/N"                          # regrava

O digest cobre o digest do prompt do core (pt-BR) + persona fiscal + template
do bloco de achados (app/agent/domain_plugins.py::domain_prompt_digest).
Regrave so depois de rodar `promptfooconfig.yaml` deste pacote.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dfe_copilot.prompt import DFE_PROMPT

from app.agent import prompts
from app.agent.domain_plugins import domain_provenance

BASELINE = Path(__file__).resolve().parents[1] / "data" / "eval" / "prompt_baseline.json"


def current() -> dict[str, str]:
    return domain_provenance(DFE_PROMPT, prompts.get_spec().digest)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--measurement", default="NAO MEDIDO: rode o promptfoo deste pacote")
    args = ap.parse_args()
    prov = current()
    if not args.write:
        print(json.dumps(prov, indent=2))
        return
    anterior = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    data = {
        # medicoes cegas sao historicas: nao se perdem ao regravar
        **{k: v for k, v in anterior.items() if k.startswith("measurement_blind")},
        "prompt_name": "dfe",
        **prov,
        "base_prompt_digest": prompts.get_spec().digest,
        "measured_by": "promptfoo (packages/dfe/promptfooconfig.yaml) via scripts/promptfoo_provider.py",
        "measurement": args.measurement,
    }
    BASELINE.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"gravado: {BASELINE}")


if __name__ == "__main__":
    main()
