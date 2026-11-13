"""DA-95: apoio de teste para produtos, sem tocar internos do core.

`diagnosis_harness` roda `run_diagnosis` com o plugin indicado e o grafo
substituido por uma funcao do teste, sem LLM, banco ou rede. Mantem os testes
de produto acoplados so a `app.sdk` (e a este modulo).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

GraphFn = Callable[[dict[str, Any]], dict[str, Any]]


@contextmanager
def diagnosis_harness(plugins: str, graph: GraphFn) -> Iterator[None]:
    """Dentro do bloco, `app.sdk.run_diagnosis` usa `plugins` (DOMAIN_PLUGINS)
    e chama `graph(estado_inicial)` no lugar do grafo LangGraph."""
    from app.agent import graph as g
    from app.agent.domain_plugins import load_plugins

    saved = (g.settings.domain_plugins, g._invoke_graph_with_timeout, g.record_incident)
    load_plugins.cache_clear()
    g.settings.domain_plugins = plugins
    g._invoke_graph_with_timeout = lambda state, timeout_seconds=None, thread_id=None: graph(state)
    g.record_incident = lambda *a, **k: None
    try:
        yield
    finally:
        g.settings.domain_plugins, g._invoke_graph_with_timeout, g.record_incident = saved
        load_plugins.cache_clear()


@contextmanager
def domain_plugins_configured(plugins: str) -> Iterator[None]:
    """Configura DOMAIN_PLUGINS no core durante o bloco (resolucao de plugin/prompt)."""
    from app.agent.domain_plugins import load_plugins
    from app.config import settings

    saved = settings.domain_plugins
    load_plugins.cache_clear()
    settings.domain_plugins = plugins
    try:
        yield
    finally:
        settings.domain_plugins = saved
        load_plugins.cache_clear()
