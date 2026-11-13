"""DA-57 — resolução da fonte de busca web aprovada por `interface_type`.

Substitui os dois mapas literais que viviam em `app/agent/nodes.py`
(`_WEB_SEARCH_SITE_MAP` e o dict `tech_term`) por uma leitura da tabela
`web_search_sources`. Ver `app/admin/models.py::WebSearchSource` para o
porquê — resumindo:

- `successfactors` e `po` nunca estado em nenhum dos dois mapas, e perdiam
  tambem o `tech_term` (caiam no genérico "SAP integration");
- `web_search_policy="approved"` nao aprovava nada (ramo que retornava
  `True` incondicionalmente).

FAIL-CLOSED, e este e o ponto que importa: sem linha na tabela, ou com a
linha desabilitada, NAO ha fonte aprovada e a busca web nao acontece para
aquele `interface_type`. Nao existe default em codigo. Um default seria
exatamente o hardcoded que a DA-57 remove, e reintroduziria a falha
silenciosa que motivou a mudanca: um conector novo aceito em todo o
produto e silenciosamente jogado num filtro generico.

Por que sessao SINCRA: `web_search_node` e `_make_web_search_tool` sao
funcoes sync, chamado de dentro de `run_diagnosis`, que roda em threadpool
(mesmo contrato de `app/admin/correlation.py` e
`app/services/incident_recorder.py`). O caminho de leitura reaproveita
`app.db.get_sync_session_factory`, que ja resolve o dialeto psycopg2.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.db import get_sync_session_factory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApprovedSource:
    """Fonte aprovada para um `interface_type` (ja habilitada)."""

    interface_type: str
    site_filter: str
    tech_term: str


def resolve_approved_source(interface_type: str | None) -> ApprovedSource | None:
    """Fonte aprovada para `interface_type`, ou `None` (fail-closed).

    Devolve `None` — e o chamador nao faz busca web — quando: nao ha
    `interface_type`; `DATABASE_URL` nao esta configurada; a tabela nao
    existe ainda (migration nao aplicada); a linha nao existe; a linha esta
    desabilitada; `site_filter`/`tech_term` vazios; ou o banco falha.

    Falha de banco e `None`, nao excecao: este e um fallback opcional do
    RAG, e derrubar o diagnóstico por causa da busca web seria o efeito
    errado. O erro fica em log para o operador ver.
    """
    if not interface_type:
        return None

    try:
        # Dentro do try de proposito: `get_sync_session_factory()` tambem
        # pode levantar (URL invalida ao criar o engine), e o docstring acima
        # promete `None` — nao excecao — para "o banco falha".
        factory = get_sync_session_factory()
        if factory is None:
            logger.debug("[web_search] sem DATABASE_URL: nenhuma fonte aprovada (fail-closed).")
            return None

        from sqlalchemy import select

        from app.admin.models import WebSearchSource

        with factory() as session:
            row = session.execute(
                select(WebSearchSource).where(WebSearchSource.interface_type == interface_type)
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 — degrade para sem busca web
        logger.warning(
            "[web_search] falha ao ler web_search_sources (interface_type=%s): %s",
            interface_type,
            exc,
        )
        return None

    if row is None:
        logger.debug(
            "[web_search] interface_type=%r sem fonte aprovada: busca web nao acontece "
            "(fail-closed). Cadastre em /admin/web-search.",
            interface_type,
        )
        return None
    if not row.enabled:
        return None
    if not (row.site_filter or "").strip() or not (row.tech_term or "").strip():
        logger.warning(
            "[web_search] fonte de %r habilitada mas sem site_filter/tech_term: ignorada.",
            interface_type,
        )
        return None

    return ApprovedSource(
        interface_type=row.interface_type,
        site_filter=row.site_filter.strip(),
        tech_term=row.tech_term.strip(),
    )


def list_approved_sources() -> dict[str, ApprovedSource]:
    """Todas as fontes habilitadas, indexadas por `interface_type`.

    Usado pelo admin (tela e health) e pelos testes. Mesma semantica
    fail-closed: sem banco devolve dict vazio, nunca um default.
    """
    factory = get_sync_session_factory()
    if factory is None:
        return {}

    try:
        from sqlalchemy import select

        from app.admin.models import WebSearchSource

        with factory() as session:
            rows = list(
                session.execute(select(WebSearchSource).where(WebSearchSource.enabled.is_(True)))
                .scalars()
                .all()
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[web_search] falha ao listar web_search_sources: %s", exc)
        return {}

    out: dict[str, ApprovedSource] = {}
    for row in rows:
        if (row.site_filter or "").strip() and (row.tech_term or "").strip():
            out[row.interface_type] = ApprovedSource(
                interface_type=row.interface_type,
                site_filter=row.site_filter.strip(),
                tech_term=row.tech_term.strip(),
            )
    return out
