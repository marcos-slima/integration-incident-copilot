#!/usr/bin/env python3
"""Provider customizado do promptfoo - roda o pipeline real do
Copilot, passando o modelo via parametro llm_model (nao mais via
mutacao de global de modulo)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.graph import run_diagnosis
from app.models import IncidentRequest


def main() -> None:
    model = sys.argv[1]
    raw_prompt = sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read()

    parts = raw_prompt.strip().split("|||")
    description = parts[0] if len(parts) > 0 else ""
    interface_type = parts[1] if len(parts) > 1 and parts[1] != "none" else None
    identifier = parts[2] if len(parts) > 2 and parts[2] != "none" else None

    request = IncidentRequest(
        description=description,
        interface_type=interface_type,
        identifier=identifier,
    )
    result = run_diagnosis(request, llm_model=model)

    print(
        json.dumps(
            {
                "matched_source": result.matched_source,
                # P1.5 (23/09/2026): DiagnosisResponse.confidence foi dividido
                # em model_confidence e diagnosis_confidence. "confidence"
                # continua como alias de diagnosis_confidence (a metrica
                # recomendada para automacao) para manter os asserts do
                # promptfooconfig.yaml validos.
                "confidence": result.diagnosis_confidence,
                "diagnosis_confidence": result.diagnosis_confidence,
                "model_confidence": result.model_confidence,
                "probable_root_cause": result.probable_root_cause,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
