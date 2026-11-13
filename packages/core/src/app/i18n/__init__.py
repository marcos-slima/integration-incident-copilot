"""DA-73: i18n das superficies servidas pelo backend (Admin UI).

Ordem de resolucao do idioma (a mesma da UI React, frontend/src/i18n):
  1. `?lng=` na URL (revisao; `xx` = pseudo-locale)
  2. cookie `iic_locale` (escolha explicita, compartilhada com a UI React)
  3. `Accept-Language` do navegador, pelo idioma base (pt-PT -> pt-BR)
  4. fallback `en`

Catalogos em `app/i18n/locales/<superficie>.<locale>.json` (JSON aninhado,
interpolacao `{{nome}}` - mesma sintaxe do i18next, para o JS do admin usar
o mesmo catalogo embutido na pagina).
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from markupsafe import Markup

logger = logging.getLogger(__name__)

SUPPORTED_LOCALES: tuple[str, ...] = ("pt-BR", "en")
FALLBACK_LOCALE = "en"
PSEUDO_LOCALE = "xx"
LOCALE_COOKIE = "iic_locale"
LOCALE_LABELS = {"pt-BR": "Português (BR)", "en": "English"}  # i18n-ignore: nome no proprio idioma

_LOCALES_DIR = Path(__file__).parent / "locales"
_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def normalize_locale(tag: str | None) -> str | None:
    """Idioma base -> locale suportado (pt-PT -> pt-BR, en-GB -> en)."""
    t = (tag or "").strip().lower()
    if t == PSEUDO_LOCALE:
        return PSEUDO_LOCALE
    if t.startswith("pt"):
        return "pt-BR"
    if t.startswith("en"):
        return "en"
    return None


def parse_accept_language(header: str | None) -> list[str]:
    """Tags do Accept-Language em ordem de preferencia (q desc, ordem estavel)."""
    items: list[tuple[float, int, str]] = []
    for i, part in enumerate((header or "").split(",")):
        piece = part.strip()
        if not piece:
            continue
        tag, _, params = piece.partition(";")
        q = 1.0
        m = re.search(r"q\s*=\s*([^;,\s]*)", params)
        if m:
            try:
                q = float(m.group(1))
            except ValueError:
                q = 0.0  # q invalido: ignora a tag, nunca a promove
        if q > 0:
            items.append((-q, i, tag.strip()))
    return [tag for _, _, tag in sorted(items)]


def negotiate(
    *, query: str | None = None, cookie: str | None = None, accept_language: str | None = None
) -> str:
    for explicit in (query, cookie):
        loc = normalize_locale(explicit)
        if loc in SUPPORTED_LOCALES or loc == PSEUDO_LOCALE:
            return loc
    for tag in parse_accept_language(accept_language):
        loc = normalize_locale(tag)
        if loc in SUPPORTED_LOCALES:  # navegador nunca liga o pseudo-locale
            return loc
    return FALLBACK_LOCALE


def locale_from_request(request: Any) -> str:
    return negotiate(
        query=request.query_params.get("lng"),
        cookie=request.cookies.get(LOCALE_COOKIE),
        accept_language=request.headers.get("accept-language"),
    )


def _pseudo(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _pseudo(v) for k, v in node.items()}
    return f"⟦{node}⟧"


@lru_cache(maxsize=16)
def load_catalog(surface: str, locale: str) -> dict[str, Any]:
    if locale == PSEUDO_LOCALE:
        return _pseudo(load_catalog(surface, FALLBACK_LOCALE))
    path = _LOCALES_DIR / f"{surface}.{locale}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _lookup(catalog: dict[str, Any], key: str) -> str | None:
    node: Any = catalog
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, str) else None


def translate(surface: str, locale: str, key: str, **values: Any) -> str:
    """Texto do catalogo; chave ausente cai no fallback e, por fim, na propria
    chave (visivel na tela - o gate `ui_i18n` impede que isso chegue ao CI)."""
    text = _lookup(load_catalog(surface, locale), key)
    if text is None and locale != FALLBACK_LOCALE:
        text = _lookup(load_catalog(surface, FALLBACK_LOCALE), key)
    if text is None:
        logger.warning("[i18n] chave ausente: %s/%s", surface, key)
        return key
    return _VAR.sub(lambda m: str(values.get(m.group(1), m.group(0))), text)


def flatten(catalog: dict[str, Any], prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in catalog.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        else:
            out[key] = v
    return out


def embed_json(catalog: dict[str, Any]) -> Markup:
    """Catalogo para `<script type="application/json">`: sem `<`, `>` e `&`
    crus, o conteudo nao fecha a tag nem vira HTML."""
    raw = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
    safe = raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return Markup(safe)  # nosec B704 - `safe` ja trocou <, > e & por escapes JSON


def template_context(request: Any, surface: str = "admin") -> dict[str, Any]:
    """Variaveis de i18n para um template Jinja."""
    locale = locale_from_request(request)

    def t(key: str, **values: Any) -> str:
        return translate(surface, locale, key, **values)

    def t_html(key: str, **values: Any) -> Markup:
        # Catalogo e arquivo do repositorio (confiavel); valores interpolados
        # sao escapados antes.
        escaped = {k: Markup.escape(v) for k, v in values.items()}
        return Markup(translate(surface, locale, key, **escaped))  # nosec B704 - valores escapados acima; catalogo e do repo

    return {
        "t": t,
        "t_html": t_html,
        "locale": locale,
        "html_lang": FALLBACK_LOCALE if locale == PSEUDO_LOCALE else locale,
        "i18n_catalog": embed_json(load_catalog(surface, locale)),
        "supported_locales": [(code, LOCALE_LABELS[code]) for code in SUPPORTED_LOCALES],
    }
