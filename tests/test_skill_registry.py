"""DA-63: skill registry declarativo (app/mcp/skill_registry.py).

Cobre o que os testes do gate nao cobrem: o comportamento em RUNTIME do
supervisor e do Capability Registry, inclusive fora da raiz do repo —
foi assim que o indice vazio passou despercebido (os testes rodam com
cwd na raiz, a imagem Docker nao tinha data/skills).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.mcp import skill_registry as sr
from app.mcp.policy import CAPABILITY_REGISTRY, ToolPolicy

REPO = Path(__file__).resolve().parent.parent


def _write(tmp_path: Path, name: str, content: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text(content, encoding="utf-8")
    return tmp_path


def test_diretorio_default_ancorado_no_repo() -> None:
    assert sr.DEFAULT_SKILLS_DIR == REPO / "data" / "skills"
    assert sr.DEFAULT_SKILLS_DIR.is_dir()


def test_skills_dir_respeita_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))
    assert sr.skills_dir() == tmp_path


def test_supervisor_roteia_fora_da_raiz_do_repo(tmp_path: Path) -> None:
    """Processo iniciado em outro diretorio continua roteando por dominio."""
    codigo = (
        "from app.agent.supervisor import classify_domain as c\n"
        "print(c({'interface_type': 'rfc'}), c({'interface_type': 'servicenow'}))\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "SKILLS_DIR"}
    env["PYTHONPATH"] = str(REPO)
    saida = subprocess.run(
        [sys.executable, "-c", codigo],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert saida == ["sap", "saas"]


def test_supervisor_falha_no_import_sem_skills(tmp_path: Path) -> None:
    """Sem skills o import falha alto — nunca roteia tudo para generic."""
    env = dict(os.environ, SKILLS_DIR=str(tmp_path / "nao-existe"), PYTHONPATH=str(REPO))
    proc = subprocess.run(
        [sys.executable, "-c", "import app.agent.supervisor"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert "indice de dominios vazio" in proc.stderr


@pytest.mark.parametrize(
    ("campo", "valor"),
    [("risk_level", "lowx"), ("data_sensitivity", "secret")],
)
def test_skill_com_valor_invalido_e_pulada(tmp_path: Path, campo: str, valor: str) -> None:
    _write(tmp_path, "x.yaml", f"name: x\n{campo}: {valor}\n")
    assert sr.load_skills(tmp_path) == {}


def test_dominio_desconhecido_e_pulado(tmp_path: Path) -> None:
    _write(tmp_path, "x.yaml", "name: x\ndomains:\n  sapp: [odata]\n")
    assert sr.load_skills(tmp_path) == {}


def test_yaml_quebrado_e_pulado_sem_derrubar_os_outros(tmp_path: Path) -> None:
    _write(tmp_path, "a.yaml", "name: [quebrado\n")
    _write(tmp_path, "b.yaml", "name: b\ndomains:\n  sap: [odata]\n")
    assert list(sr.load_skills(tmp_path)) == ["b"]


def test_interface_type_em_dois_dominios_e_ambiguidade() -> None:
    skills = {
        "a": {"name": "a", "domains": {"sap": ["odata"]}},
        "b": {"name": "b", "domains": {"saas": ["odata"]}},
    }
    with pytest.raises(ValueError, match="dois dominios"):
        sr.build_domain_index(skills)


def test_indice_real_cobre_os_nove_conectores_roteaveis() -> None:
    from app.connectors import _REGISTRY

    indice = sr.build_domain_index()
    # apim e cross-vendor por decisao: cai em generic (gate connector_reachable).
    assert set(indice) == set(_REGISTRY) - {"apim"}


def test_capability_registry_vem_dos_yamls_com_os_valores_do_fallback() -> None:
    """Os YAMLs e o dict estatico de app/mcp/policy.py nao podem divergir:
    o fallback so existe para ambiente sem skills, e com valores diferentes
    a mesma tool teria politicas diferentes conforme o ambiente."""
    assert CAPABILITY_REGISTRY == {
        "diagnose_incident": ToolPolicy(
            name="diagnose_incident",
            risk_level="low",
            destructive=False,
            scopes_required=("integration.read",),
            approval_required=False,
            data_sensitivity="confidential",
            timeout_seconds=180.0,
            max_retries=1,
        ),
        "list_connectors": ToolPolicy(
            name="list_connectors",
            risk_level="low",
            destructive=False,
            scopes_required=("integration.read",),
            approval_required=False,
            data_sensitivity="public",
            timeout_seconds=5.0,
        ),
    }


def test_dockerfile_copia_as_skills() -> None:
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY --chown=appuser:appuser data/skills/ data/skills/" in dockerfile
