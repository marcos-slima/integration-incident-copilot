"""DA-74: idioma do CONTEUDO do diagnostico.

As interfaces (DA-73) seguem o idioma do navegador; o conteudo que o
backend produz - causa raiz e proximos passos do rule engine, avisos dos
guardrails de confianca, relatorio em Markdown e mensagens de ativacao -
segue o `locale` pedido pelo cliente (`IncidentRequest.locale`).

- `pt-BR` e' o default e a fonte: as regras (`app/ontology/error_codes.ttl`
  e `app/agent/rules.py`) continuam escritas em portugues, e o catalogo
  `content.pt-BR.json` reproduz byte a byte os textos de antes desta DA.
- `en` vem de `content.en.json`; as regras ficam em `rules.<categoria>`.
- Texto ausente no locale cai no `pt-BR` (nunca na chave crua): o
  conteudo de um diagnostico nao pode sumir por falta de traducao. O gate
  `content_i18n` impede que isso chegue ao CI.
"""

from __future__ import annotations

import json
import logging
import re
from functools import cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Idiomas do conteudo do diagnostico (o pseudo-locale `xx` da DA-73 e' so
#: de tela; aqui ele nao existe).
CONTENT_LOCALES: tuple[str, ...] = ("pt-BR", "en")
DEFAULT_CONTENT_LOCALE = "pt-BR"

_LOCALES_DIR = Path(__file__).parent / "locales"
_VAR = re.compile(r"\{\{(\w+)\}\}")


def content_locale(locale: str | None) -> str:
    """Locale suportado para o conteudo; ausente ou desconhecido -> pt-BR."""
    return locale if locale in CONTENT_LOCALES else DEFAULT_CONTENT_LOCALE


@cache
def content_catalog(locale: str) -> dict[str, Any]:
    path = _LOCALES_DIR / f"content.{locale}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _lookup(catalog: dict[str, Any], key: str) -> Any:
    node: Any = catalog
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def content_text(locale: str | None, key: str, **values: Any) -> str:
    """Texto do catalogo de conteudo, com `{{var}}` interpolado numa passada
    (o valor inserido nao e' reinterpretado)."""
    loc = content_locale(locale)
    text = _lookup(content_catalog(loc), key)
    if not isinstance(text, str) and loc != DEFAULT_CONTENT_LOCALE:
        logger.warning("[i18n] conteudo sem traducao: %s/%s", loc, key)
        text = _lookup(content_catalog(DEFAULT_CONTENT_LOCALE), key)
    if not isinstance(text, str):
        logger.warning("[i18n] chave de conteudo ausente: %s", key)
        return key
    return _VAR.sub(lambda m: str(values.get(m.group(1), m.group(0))), text)


def localize_rule(
    category: str, root_cause: str, next_steps: list[str], locale: str | None
) -> tuple[str, list[str]]:
    """Causa raiz e passos de uma regra do rule engine no idioma pedido.

    pt-BR devolve o texto da propria regra (TTL/rules.py). Outro idioma usa
    `rules.<categoria>` do catalogo; sem entrada, devolve o pt-BR e loga."""
    loc = content_locale(locale)
    if loc == DEFAULT_CONTENT_LOCALE:
        return root_cause, list(next_steps)
    entry = _lookup(content_catalog(loc), f"rules.{category}")
    if not isinstance(entry, dict) or not entry.get("root_cause"):
        logger.warning("[i18n] regra sem traducao: %s/%s", loc, category)
        return root_cause, list(next_steps)
    return entry["root_cause"], list(entry.get("next_steps") or next_steps)
