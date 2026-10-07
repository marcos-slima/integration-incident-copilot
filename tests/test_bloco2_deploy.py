"""Validacao 2026-10-07, Bloco 2 (DEP-01): docs desligaveis e gate do modelo de producao."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.evaluation.gates import check_deployed_model_evaluated

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(("env", "esperado"), [("true", True), ("false", False)])
def test_expose_api_docs_controla_openapi(env, esperado):
    """Subprocesso: recarregar app.main dentro da suite trocaria o objeto `app`
    que outros testes ja importaram."""
    codigo = "from app.main import app; print(app.openapi_url, app.docs_url, app.redoc_url)"
    saida = (
        subprocess.run(
            [sys.executable, "-c", codigo],
            env={**os.environ, "EXPOSE_API_DOCS": env, "PYTHONPATH": str(REPO)},
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        .stdout.strip()
        .splitlines()[-1]
    )
    if esperado:
        assert saida == "/openapi.json /docs /redoc"
    else:
        assert saida == "None None None"


def _repo(tmp_path: Path, llm_model: str | None, baseline: dict | None) -> Path:
    kyma = tmp_path / "deploy" / "kyma"
    kyma.mkdir(parents=True)
    data = f'  LLM_MODEL: "{llm_model}"\n' if llm_model else "  OUTRA: x\n"
    (kyma / "configmap.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\ndata:\n" + data,
        encoding="utf-8",
    )
    if baseline is not None:
        eval_dir = tmp_path / "data" / "eval"
        eval_dir.mkdir(parents=True)
        (eval_dir / "promptfoo_baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("llm_model", "baseline", "severidade"),
    [
        ("gpt-4o-mini", None, "warn"),
        ("gpt-4o-mini", {"model": None, "cases": {}}, "warn"),
        ("gpt-4o-mini", {"model": "qwen3:8b", "cases": {}}, "warn"),
        ("gpt-4o-mini", {"model": "gpt-4o-mini", "cases": {}}, "pass"),
        (None, None, "warn"),
    ],
)
def test_gate_modelo_de_producao(tmp_path, llm_model, baseline, severidade):
    findings = check_deployed_model_evaluated(_repo(tmp_path, llm_model, baseline))
    assert [f.severity for f in findings] == [severidade]


def test_writer_grava_o_modelo(tmp_path):
    from scripts.quality_gate import _write_baseline

    resultados = tmp_path / "r.json"
    resultados.write_text(
        json.dumps({"results": [{"pass": True, "testCase": {"description": "c1"}}]}),
        encoding="utf-8",
    )
    destino = tmp_path / "b.json"
    assert _write_baseline(resultados, destino, "gpt-4o-mini") == 0
    assert json.loads(destino.read_text(encoding="utf-8"))["model"] == "gpt-4o-mini"
