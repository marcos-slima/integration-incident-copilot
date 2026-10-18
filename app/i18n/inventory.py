"""DA-73: inventario de texto visivel fora dos catalogos de i18n.

Heuristico de proposito: procura o que o usuario LE e que nao passou por
`t(...)`. Tres superficies:

- React (`frontend/src/**/*.ts(x)`): texto entre tags JSX, atributos
  visiveis (placeholder, title, aria-label, alt) e literais de string com
  duas ou mais palavras.
- Templates Jinja (`app/admin/templates/*.html`): texto entre tags e os
  mesmos atributos, fora de `{{ }}`, `{% %}`, `<script>` e `<style>`.
- JS do admin (`app/admin/static/*.js`): literais com duas ou mais palavras.

Falso positivo se suprime com `i18n-ignore` na mesma linha (comentario),
sempre com o motivo. Nao cobre texto montado por concatenacao de
fragmentos de uma palavra - o catalogo proibe concatenar frases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_LETTER = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
_TWO_WORDS = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]{2,}[^A-Za-zÀ-ÖØ-öø-ÿ\n]+[A-Za-zÀ-ÖØ-öø-ÿ]{2,}")
_VISIBLE_ATTRS = re.compile(
    r"""\b(placeholder|title|aria-label|alt)\s*=\s*(["'])([^"'{}]*?)\2""", re.IGNORECASE
)
_JSX_TEXT = re.compile(r">([^<>{}`]+)<")
# So aspas simples/duplas: template literals (`...`) aninham ${...} com
# outros literais e nao se deixam recortar por regex. O HTML montado neles e
# coberto por scan_markup (texto entre tags), que tambem roda no JS.
_STRING = re.compile(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"")
# Literais que nunca sao texto para o usuario
_NOT_TEXT = re.compile(
    r"""^(?:
        [\w./:@-]*                      # identificador, caminho, URL, chave
      | [\w-]+(?:\s+[\w-]+)*\s*[:;].*  # CSS / estilo
      | .*\b(?:var|calc|rgba?|url)\(.*  # CSS
      | .*\d(?:px|ms|s|rem|em)\b.*      # medida CSS (2px solid, .9s linear)
      | (?:\.\w+[\s,·]*)+               # lista de extensoes (.txt · .log)
    )$""",
    re.VERBOSE,
)
_CODE_LINE = re.compile(
    r"^\s*(import|export\s+\*|from)\b|className=|style=|fontFamily|console\.|"
    r"querySelector|getElementById|addEventListener|new URLSearchParams|"
    r"adminFetch\(|fetch\(|data-click|data-args|encodeURIComponent"
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.text}"


def _strip_js_comments(src: str) -> str:
    # preserva quebras de linha para manter a numeracao
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.DOTALL)
    return re.sub(r"(?m)(^|[^:\\])//(?!.*i18n-ignore).*$", r"\1", src)


def _blank_jinja(src: str) -> str:
    for pat in (
        r"\{#.*?#\}",
        r"\{%.*?%\}",
        r"\{\{.*?\}\}",
        r"<script\b.*?</script>",
        r"<style\b.*?</style>",
        r"<!--.*?-->",
    ):
        src = re.sub(pat, lambda m: re.sub(r"[^\n]", " ", m.group(0)), src, flags=re.DOTALL)
    return src


_TECH_TOKEN = re.compile(r"^[a-z0-9_./$:+-]+(?:\s\*)?$")


def _strip_interpolations(text: str) -> str:
    """Remove ${...} com chaves balanceadas (template literal do JS)."""
    out, i, depth = [], 0, 0
    while i < len(text):
        if text.startswith("${", i):
            depth += 1
            i += 2
            continue
        if depth and text[i] == "{":
            depth += 1
        elif depth and text[i] == "}":
            depth -= 1
            i += 1
            if depth == 0:
                out.append(" ")
            continue
        if not depth:
            out.append(text[i])
        i += 1
    return "".join(out)


def _techy(value: str) -> bool:
    """Classe CSS, seletor, caminho: todo token tem - _ / [ ] ? = ou ."""
    tokens = value.split()
    return (
        bool(tokens)
        and not re.search(r"[A-ZÀ-ÖØ-öø-ÿ]", value)
        and all(re.search(r"[-_/\[\]?=.:]", tok) for tok in tokens)
    )


def _is_text(value: str) -> bool:
    v = value.strip()
    if not v or not _LETTER.search(v):
        return False
    # identificador/valor de enum exibido como codigo (odata, base_url, prod)
    if _TECH_TOKEN.match(v):
        return False
    return not (v.startswith("&") and v.endswith(";"))  # entidade HTML


def _line_of(src: str, pos: int) -> int:
    return src.count("\n", 0, pos) + 1


def _ignored(lines: list[str], line: int) -> bool:
    return "i18n-ignore" in lines[line - 1]


def scan_markup(path: Path, src: str, *, jinja: bool) -> list[Finding]:
    """Texto entre tags e atributos visiveis."""
    raw_lines = src.splitlines()
    body = _blank_jinja(src) if jinja else _strip_js_comments(src)
    out: list[Finding] = []
    for m in _JSX_TEXT.finditer(body):
        text = _strip_interpolations(m.group(1)) if "${" in m.group(1) else m.group(1)
        if "`" in text or not _is_text(text) or _techy(text.strip()):
            continue
        # JSX: ignora generics/comparacoes de TypeScript (a < b > c)
        if not jinja and re.search(r"[=;()]|=>|\|\||&&", text):
            continue
        line = _line_of(body, m.start(1) + len(text) - len(text.lstrip()))
        if not _ignored(raw_lines, line):
            out.append(Finding(str(path), line, " ".join(text.split())))
    for m in _VISIBLE_ATTRS.finditer(body):
        if _is_text(m.group(3)):
            line = _line_of(body, m.start())
            if not _ignored(raw_lines, line):
                out.append(Finding(str(path), line, f'{m.group(1)}="{m.group(3)}"'))
    return out


def scan_literals(path: Path, src: str) -> list[Finding]:
    """Literais de string com 2+ palavras, fora de linhas de codigo puro."""
    raw_lines = src.splitlines()
    body = _strip_js_comments(src)
    out: list[Finding] = []
    for m in _STRING.finditer(body):
        value = next(g for g in m.groups() if g is not None)
        line = _line_of(body, m.start())
        code_line = raw_lines[line - 1] if line <= len(raw_lines) else ""
        if _ignored(raw_lines, line) or _CODE_LINE.search(code_line):
            continue
        if "${" in value:
            continue  # atributo dentro de template literal: valor interpolado
        value = re.sub(r"<[^>]*>", " ", value).strip()
        if not value or _NOT_TEXT.match(value) or _techy(value):
            continue
        # uma palavra so conta se tiver acento ou inicial maiuscula de texto
        if not _TWO_WORDS.search(value) and not re.fullmatch(
            r"[A-ZÀ-ÖØ-Þ][a-zß-öø-ÿ]+[.…!?:]*|.*[À-ÖØ-öø-ÿ].*", value
        ):
            continue
        out.append(Finding(str(path), line, value[:80]))
    return out


def scan_repo(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for p in sorted((root / "frontend/src").rglob("*.ts*")):
        if "i18n" in p.parts or p.name.endswith(".d.ts"):
            continue
        src = p.read_text(encoding="utf-8")
        rel = p.relative_to(root)
        if p.suffix == ".tsx":
            findings += scan_markup(rel, src, jinja=False)
        findings += scan_literals(rel, src)
    for p in sorted((root / "app/admin/templates").glob("*.html")):
        findings += scan_markup(p.relative_to(root), p.read_text(encoding="utf-8"), jinja=True)
    for p in sorted((root / "app/admin/static").glob("*.js")):
        src = p.read_text(encoding="utf-8")
        findings += scan_markup(p.relative_to(root), src, jinja=False)
        findings += scan_literals(p.relative_to(root), src)
    # dedup (mesma linha achada por duas regras)
    seen: set[tuple[str, int, str]] = set()
    unique = []
    for f in findings:
        key = (f.path, f.line, f.text)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique
