"""DA-63: declarative skill registry — skills como artefatos YAML.

Este modulo carrega skills declarativas de ``data/skills/*.yaml`` e expoe:

- ``load_skills()`` — dicionario {name: skill_dict} carregado do disco.
- ``build_domain_index()`` — mapeia interface_type -> dominio (sap|saas).

Consumidores: ``app/mcp/policy.py`` (CAPABILITY_REGISTRY, DA-27),
``app/agent/supervisor.py`` (roteamento, DA-22) e o gate
``connector_reachable`` (``app/evaluation/gates.py``). Os tres usam este
modulo, para que exista um so parser do formato. Ele importa so ``yaml``
e a stdlib, entao continua leve para o quality gate.

O diretorio default e' ancorado no proprio pacote (``<repo>/data/skills``),
nao no diretorio corrente: com o caminho relativo, qualquer processo
iniciado fora da raiz do repo — ou uma imagem sem ``data/skills`` —
montava um indice vazio e roteava TODO interface_type para ``generic``
sem erro nenhum (achado na revisao de 2026-10-14). ``SKILLS_DIR`` continua
valendo como override explicito.

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

Skill com campo invalido e' PULADA com warning (nunca recebe default
permissivo): no Capability Registry, que e' fail-closed, isso significa
tool negada; no supervisor, indice sem a skill de diagnostico faz o
import falhar (``app/agent/supervisor.py``).

Nao-objetivo: este modulo NAO carrega MCP servers externos (Fase B do
DA-19). So lida com skills locais declaradas neste repositorio.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_SKILLS_DIR = Path(__file__).resolve().parents[2] / "data" / "skills"

# Valores aceitos — espelham os Literals de app/mcp/policy.py (RiskLevel,
# DataSensitivity) e os dominios de sub-agente do supervisor. "generic"
# fica de fora de proposito: e' o destino de quem NAO esta no indice.
RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})
DATA_SENSITIVITIES = frozenset({"public", "confidential"})
ROUTABLE_DOMAINS = frozenset({"sap", "saas"})


def skills_dir() -> Path:
    """Diretorio efetivo: ``SKILLS_DIR`` se definido, senao o do repo."""
    override = os.environ.get("SKILLS_DIR")
    return Path(override) if override else DEFAULT_SKILLS_DIR


def validate_skill(data: Any) -> list[str]:
    """Problemas de schema de uma skill (lista vazia = valida)."""
    if not isinstance(data, dict):
        return ["YAML nao e' mapeamento"]
    problemas: list[str] = []
    if not data.get("name"):
        problemas.append("sem campo 'name'")
    if "risk_level" in data and data["risk_level"] not in RISK_LEVELS:
        problemas.append(f"risk_level invalido: {data['risk_level']!r}")
    if "data_sensitivity" in data and data["data_sensitivity"] not in DATA_SENSITIVITIES:
        problemas.append(f"data_sensitivity invalido: {data['data_sensitivity']!r}")
    domains = data.get("domains") or {}
    if not isinstance(domains, dict):
        problemas.append("'domains' nao e' mapeamento")
    else:
        for domain, interface_types in domains.items():
            if domain not in ROUTABLE_DOMAINS:
                problemas.append(f"dominio invalido: {domain!r} (aceitos: sap, saas)")
            if not isinstance(interface_types, list) or not all(
                isinstance(it, str) for it in interface_types
            ):
                problemas.append(f"domains.{domain} nao e' lista de strings")
    return problemas


def load_skills(directory: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """Carrega todos os *.yaml de ``directory`` e retorna {name: skill_dict}.

    Arquivo ilegivel ou com schema invalido e' pulado com warning.
    """
    base = Path(directory) if directory is not None else skills_dir()
    if not base.is_dir():
        logger.warning("skill_registry: diretorio de skills inexistente: %s", base)
        return {}

    skills: dict[str, dict[str, Any]] = {}
    for path in sorted(base.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError) as exc:
            logger.warning("skill_registry: %s ilegivel — pulado: %s", path.name, exc)
            continue
        problemas = validate_skill(data)
        if problemas:
            logger.warning("skill_registry: %s invalido — pulado: %s", path.name, problemas)
            continue
        skills[data["name"]] = data
    return skills


def build_domain_index(
    skills: dict[str, dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Deriva mapeamento interface_type -> dominio de todas as skills.

    Retorna ``{"odata": "sap", "rfc": "sap", "servicenow": "saas", ...}``.
    Um interface_type declarado em dois dominios e' ambiguidade, e o
    roteamento nao escolhe por ordem de arquivo: levanta ``ValueError``.
    """
    if skills is None:
        skills = load_skills()

    index: dict[str, str] = {}
    for skill_name in sorted(skills):
        domains = skills[skill_name].get("domains") or {}
        for domain, interface_types in domains.items():
            for it in interface_types:
                chave = it.lower()
                anterior = index.get(chave)
                if anterior is not None and anterior != domain:
                    raise ValueError(
                        f"skill_registry: interface_type {chave!r} declarado em "
                        f"dois dominios ({anterior!r} e {domain!r})"
                    )
                index[chave] = domain
    return index
