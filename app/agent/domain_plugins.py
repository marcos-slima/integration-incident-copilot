"""DA-94: ponto de extensao de produto de dominio sobre o core do IIC.

O core nao conhece nenhum dominio. Um produto (ex.: DF-e Copilot, em
`packages/dfe`) entrega um modulo com um objeto `PLUGIN` que implementa
`DomainPlugin`; o core o carrega por `DOMAIN_PLUGINS`. Regras:

- core nunca importa um produto; so o carrega pelo nome configurado;
- plugin configurado que nao carrega derruba o build do grafo (fail-closed);
- `run()` so devolve findings deterministicos (`kind="fact"` exige fonte
  tool/connector/rule_reference - ver app/agent/findings.py);
- falha em `run()` vira finding de lacuna, nunca excecao no grafo.
"""

from __future__ import annotations

import importlib
import logging
from functools import lru_cache
from typing import Any, Protocol, runtime_checkable

_logger = logging.getLogger(__name__)


@runtime_checkable
class DomainPlugin(Protocol):
    name: str

    def validate_context(self, context: dict[str, Any]) -> dict[str, Any] | None:
        """Contexto normalizado se este plugin aceita o contexto; None se nao e' dele."""

    def run(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Findings deterministicos para o estado (sem LLM, sem escrita externa)."""


class DomainPluginError(RuntimeError):
    pass


class DomainContextError(ValueError):
    """Contexto de dominio recusado pelo plugin (erro de entrada, 422)."""


@lru_cache(maxsize=8)
def load_plugins(spec: str) -> tuple[DomainPlugin, ...]:
    plugins: list[DomainPlugin] = []
    for module_name in [m.strip() for m in spec.split(",") if m.strip()]:
        try:
            plugin = importlib.import_module(module_name).PLUGIN
        except (ImportError, AttributeError) as exc:
            raise DomainPluginError(
                f"plugin de dominio nao carregou: {module_name}: {exc}"
            ) from exc
        if not isinstance(plugin, DomainPlugin):
            raise DomainPluginError(f"{module_name}.PLUGIN nao implementa DomainPlugin")
        plugins.append(plugin)
    return tuple(plugins)


def resolve_context(
    plugins: tuple[DomainPlugin, ...], context: dict[str, Any] | None
) -> tuple[str | None, dict[str, Any] | None]:
    """Primeiro plugin que aceita o contexto; contexto recusado levanta DomainContextError."""
    if not context or not plugins:
        return None, None
    for plugin in plugins:
        try:
            normalized = plugin.validate_context(context)
        except ValueError as exc:  # pydantic.ValidationError herda de ValueError
            raise DomainContextError(f"{plugin.name}: {exc}") from exc
        if normalized is not None:
            return plugin.name, normalized
    raise DomainContextError("nenhum plugin de dominio aceitou o contexto")


def make_domain_tools_node(plugins: tuple[DomainPlugin, ...]):
    by_name = {p.name: p for p in plugins}

    def domain_tools_node(state: dict[str, Any]) -> dict[str, Any]:
        plugin = by_name.get(state.get("domain_plugin") or "")
        if plugin is None:
            return {"domain_findings": []}
        try:
            return {"domain_findings": list(plugin.run(state))}
        except Exception as exc:  # noqa: BLE001 - plugin nunca derruba o diagnostico
            _logger.warning("domain_tools: plugin %s falhou: %s", plugin.name, exc)
            return {
                "domain_findings": [
                    {
                        "id": "G-plugin",
                        "kind": "fact",
                        "source": "tool",
                        "statement": f"Ferramentas do dominio '{plugin.name}' falharam; sem fatos de dominio.",
                        "evidence_refs": [],
                    }
                ]
            }

    return domain_tools_node
