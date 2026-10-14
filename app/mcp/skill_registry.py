"""DA-63: declarative skill registry — skills como artefatos YAML.

Este modulo carrega skills declarativas de ``data/skills/*.yaml`` e expoe:

- ``load_skills()`` — dicionario {name: skill_dict} carregado do disco.
- ``build_domain_index()`` — mapeia interface_type -> dominio (sap|saas).

Nao importa nada de app.mcp.policy para evitar circular import.
O builder de ToolPolicy fica em policy.py, que pode importar deste modulo.

Formato YAML esperado por skill::

    name: <string>
    version: "<semver>"
    risk_level: low | medium | high | critical
    destructive: true | false
    scopes_required:
      - <scope>
    approval_required: true | false
    data_sensitivity: public | confidential
    timeout_seconds: <float>
    max_retries: <int>
    domains:
      sap: [interface1, interface2]
      saas: [interface3, interface4]

ONCE A MIGRACAO COMPLETA (todos os conectores declarados em YAML), o
supervisor (app/agent/supervisor.py) consulta este registry para derivar
os conjuntos de roteamento ao invés de usar _SAP_INTERFACE_TYPES /
_SAAS_INTERFACE_TYPES hardcoded.

Nao-objetivo: este modulo NAO carrega MCP servers externos (Fase B do
DA-19). So lida com skills locais declaradas neste repositorio.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_SKILLS_DIR = Path(os.environ.get("SKILLS_DIR", "data/skills"))


def load_skills(directory: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """Carrega todos os *.yaml de ``directory`` e retorna {name: skill_dict}.

    Cada arquivo YAML deve conter as chaves definidas no schema DA-63.
    Arquivos com schema invalido sao ignorados com warning em stderr.
    """
    skills_dir = Path(directory) if directory is not None else _SKILLS_DIR
    if not skills_dir.is_dir():
        return {}

    skills: dict[str, dict[str, Any]] = {}
    for path in sorted(skills_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                print(f"[skill_registry] {path.name}: YAML nao e' mapeamento — pulado", flush=True)
                continue
            name = data.get("name")
            if not name:
                print(f"[skill_registry] {path.name}: sem campo 'name' — pulado", flush=True)
                continue
            skills[name] = data
        except yaml.YAMLError as exc:
            print(f"[skill_registry] {path.name}: erro YAML — pulado: {exc}", flush=True)
        except (OSError, KeyError, TypeError):
            print(f"[skill_registry] {path.name}: erro inesperado — pulado", flush=True)

    return skills


def build_domain_index(
    skills: dict[str, dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Deriva mapeamento interface_type -> dominio de todas as skills.

    Retorna ``{"odata": "sap", "rfc": "sap", "servicenow": "saas", ...}``.
    Se um interface_type aparece em mais de um dominio, o ultimo vence
    (ordem alfabetica do nome da skill).
    """
    if skills is None:
        skills = load_skills()

    index: dict[str, str] = {}
    for skill_name in sorted(skills):
        domains = skills[skill_name].get("domains") or {}
        for domain, interface_types in domains.items():
            for it in interface_types:
                index[it.lower()] = domain
    return index
