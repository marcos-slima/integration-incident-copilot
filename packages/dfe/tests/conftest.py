"""Isolamento do produto DF-e: reaproveita o do core (tests/conftest.py).

Sem isto, os testes de `packages/dfe/tests` rodavam SEM as variaveis e
fixtures de isolamento do core (DATABASE_URL/REDIS_URL/NEO4J vazios, flags
desligadas, rate limiter e circuit breaker resetados). No CI, sem `.env`,
o `run_diagnosis` tentava alcancar servicos pelos defaults e o job travava.
Hooks `pytest_*` do core nao sao copiados (ja rodam pelo conftest original).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_CORE = Path(__file__).resolve().parents[3] / "tests" / "conftest.py"
_spec = importlib.util.spec_from_file_location("_core_conftest", _CORE)
_core = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_core)  # type: ignore[union-attr]

globals().update({k: v for k, v in vars(_core).items() if not k.startswith(("__", "pytest_"))})
