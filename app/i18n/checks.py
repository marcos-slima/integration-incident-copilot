"""DA-73: verificacoes do gate `ui_i18n` (funcoes puras sobre o repositorio).

1. Paridade: todo locale de uma superficie tem as mesmas chaves e, em cada
   chave, as mesmas variaveis {{x}}.
2. Chaves usadas existem: `t('chave')` no React, nos templates Jinja e no JS
   do admin; prefixo dinamico (`t(\\`details.trust.${x}\\`)`,
   `'reasons.' + code`) tem que existir como grupo.
3. Nenhum texto visivel fora do catalogo (app/i18n/inventory.py).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.i18n import flatten
from app.i18n.inventory import scan_repo

SURFACES = {
    "web": (Path("frontend/src/i18n/locales"), "{locale}.json"),
    "admin": (Path("app/i18n/locales"), "admin.{locale}.json"),
}
LOCALES = ("pt-BR", "en")
_VARS = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_PLURAL = ("_zero", "_one", "_two", "_few", "_many", "_other")


def _load(root: Path, surface: str) -> dict[str, dict[str, str]]:
    folder, pattern = SURFACES[surface]
    out = {}
    for loc in LOCALES:
        path = root / folder / pattern.format(locale=loc)
        out[loc] = flatten(json.loads(path.read_text(encoding="utf-8")))
    return out


def parity_problems(catalogs: dict[str, dict[str, str]], surface: str) -> list[str]:
    problems: list[str] = []
    base_loc, base = next(iter(catalogs.items()))
    for loc, cat in catalogs.items():
        if loc == base_loc:
            continue
        for key in sorted(set(base) - set(cat)):
            problems.append(f"{surface}: chave '{key}' falta em {loc}")
        for key in sorted(set(cat) - set(base)):
            problems.append(f"{surface}: chave '{key}' so existe em {loc}")
        for key in sorted(set(base) & set(cat)):
            if set(_VARS.findall(base[key])) != set(_VARS.findall(cat[key])):
                problems.append(f"{surface}: variaveis de '{key}' diferem entre {base_loc} e {loc}")
    return problems


def _exists(key: str, flat: dict[str, str]) -> bool:
    return key in flat or any(key + suffix in flat for suffix in _PLURAL)


def _group_exists(prefix: str, flat: dict[str, str]) -> bool:
    return any(k.startswith(prefix + ".") for k in flat)


_WEB_STATIC = re.compile(r"""\b(?:i18n\.)?t\(\s*['"]([\w.]*\w)['"]\s*[,)]""")
_WEB_DATA_KEYS = re.compile(r"""['"]((?:systems|demo|badge)\.\w+)['"]""")
_WEB_DYNAMIC = re.compile(r"""\bt\(\s*`([\w.]+)\.\$\{""")
_ADMIN_STATIC = re.compile(r"""\bt(?:_html)?\(\s*['"]([\w.]*\w)['"]\s*[,)]""")
_ADMIN_DYNAMIC = re.compile(r"""['"]([\w.]*\w)\.['"]\s*\+""")


def used_key_problems(root: Path, catalogs: dict[str, dict[str, str]], surface: str) -> list[str]:
    flat = catalogs[LOCALES[0]]
    problems: list[str] = []
    if surface == "web":
        files = [p for p in (root / "frontend/src").rglob("*.ts*") if "i18n" not in p.parts]
        static, dynamic = (_WEB_STATIC, _WEB_DATA_KEYS), (_WEB_DYNAMIC,)
    else:
        files = sorted((root / "app/admin/templates").glob("*.html")) + sorted(
            (root / "app/admin/static").glob("*.js")
        )
        static, dynamic = (_ADMIN_STATIC,), (_ADMIN_DYNAMIC,)
    for path in files:
        src = path.read_text(encoding="utf-8")
        rel = path.relative_to(root)
        for rx in static:
            for key in rx.findall(src):
                if "." in key and not _exists(key, flat):
                    problems.append(f"{surface}: {rel} usa a chave '{key}', ausente do catalogo")
        for rx in dynamic:
            for prefix in rx.findall(src):
                if not _group_exists(prefix, flat):
                    problems.append(
                        f"{surface}: {rel} usa o grupo '{prefix}.*', ausente do catalogo"
                    )
    return problems


def ui_i18n_problems(root: Path) -> list[str]:
    problems: list[str] = []
    for surface in SURFACES:
        try:
            catalogs = _load(root, surface)
        except (OSError, ValueError) as exc:
            problems.append(f"{surface}: catalogo ilegivel ({exc})")
            continue
        problems += parity_problems(catalogs, surface)
        problems += used_key_problems(root, catalogs, surface)
    problems += [f"texto fora do catalogo: {f}" for f in scan_repo(root)]
    return problems


# ---------------------------------------------------------------------------
# DA-74: conteudo do diagnostico (gate `content_i18n`)
# ---------------------------------------------------------------------------

CONTENT_DIR = Path("app/i18n/locales")


def _content(root: Path, locale: str) -> dict:
    return json.loads((root / CONTENT_DIR / f"content.{locale}.json").read_text(encoding="utf-8"))


def rule_categories(root: Path) -> set[str]:
    """Categorias que o rule engine pode devolver: TTL + regras em codigo."""
    from app.agent.ontology_loader import load_error_rules
    from app.agent.rules import KNOWN_ERROR_RULES

    ttl = root / "app/ontology/error_codes.ttl"
    return {r.category for r in load_error_rules(ttl)} | {r.category for r in KNOWN_ERROR_RULES}


def content_i18n_problems(root: Path) -> list[str]:
    """1. Paridade de chaves/variaveis entre os catalogos de conteudo (fora
    `rules`, que so existe nos idiomas diferentes do default).
    2. Toda categoria do rule engine tem causa raiz e passos em cada idioma
    nao-default.
    3. Todo idioma de conteudo tem prompt (instrucao de idioma) e existe nas
    interfaces."""
    from app.agent.prompts import prompt_locales
    from app.i18n import SUPPORTED_LOCALES
    from app.i18n.content import CONTENT_LOCALES, DEFAULT_CONTENT_LOCALE

    problems: list[str] = []
    try:
        catalogs = {loc: _content(root, loc) for loc in CONTENT_LOCALES}
    except (OSError, ValueError) as exc:
        return [f"content: catalogo ilegivel ({exc})"]

    flat = {
        loc: {k: v for k, v in flatten(cat).items() if not k.startswith("rules.")}
        for loc, cat in catalogs.items()
    }
    problems += parity_problems(flat, "content")

    categories = rule_categories(root)
    for loc, cat in catalogs.items():
        if loc == DEFAULT_CONTENT_LOCALE:
            continue
        rules = cat.get("rules") or {}
        for category in sorted(categories):
            entry = rules.get(category)
            if not isinstance(entry, dict) or not entry.get("root_cause"):
                problems.append(f"content: regra '{category}' sem causa raiz em {loc}")
            elif not entry.get("next_steps"):
                problems.append(f"content: regra '{category}' sem proximos passos em {loc}")
        for extra in sorted(set(rules) - categories):
            problems.append(f"content: regra '{extra}' em {loc} nao existe no rule engine")

    missing_prompt = set(CONTENT_LOCALES) - set(prompt_locales())
    for loc in sorted(missing_prompt):
        problems.append(f"content: idioma '{loc}' sem instrucao de idioma no prompt")
    for loc in sorted(set(CONTENT_LOCALES) - set(SUPPORTED_LOCALES)):
        problems.append(f"content: idioma '{loc}' nao existe nas interfaces (SUPPORTED_LOCALES)")
    return problems
