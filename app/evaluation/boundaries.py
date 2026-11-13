"""DA-95: fronteiras de import do monorepo (gate `import_boundaries`).

Le `architecture/boundaries.toml` e devolve as violacoes. Funcoes puras
sobre o repositorio, sem importar nenhum modulo verificado (o gate tem de
pegar exatamente o acoplamento que impediria separar um produto).

Python: `ast` (import e from-import, inclusive dentro de funcao). TypeScript:
regex sobre `import ... from '...'`, `import '...'` e `import('...')`.
"""

from __future__ import annotations

import ast
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

POLICY = Path("architecture/boundaries.toml")
_TS_IMPORT = re.compile(r"""(?:\bfrom\s+|\bimport\s+|\bimport\(\s*)['"]([^'"]+)['"]""")
_SKIP = {"node_modules", "__pycache__", "dist", ".venv"}


@dataclass(frozen=True)
class Violation:
    rule: str
    file: str
    line: int
    target: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.file}:{self.line} importa '{self.target}'"


def _files(root: Path, paths: list[str], suffixes: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        base = root / p
        if base.is_file():
            out.append(base)
        elif base.is_dir():
            out += [f for f in base.rglob("*") if f.suffix in suffixes and not _SKIP & set(f.parts)]
    return sorted(out)


def python_imports(source: str) -> list[tuple[int, str]]:
    """(linha, modulo) de cada import; `from a import b` conta como `a.b`
    e como `a` (o nome pode ser submodulo ou atributo)."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found += [(node.lineno, f"{node.module}.{a.name}") for a in node.names]
    return found


def _under(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _check_python(root: Path, rule: dict) -> list[Violation]:
    out: list[Violation] = []
    forbid = rule.get("forbid", [])
    only = rule.get("only_via")
    for f in _files(root, rule["paths"], (".py",)):
        try:
            imports = python_imports(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = str(f.relative_to(root))
        for line, mod in imports:
            if any(_under(mod, p) for p in forbid) or (
                only
                and _under(mod, only["root"])
                # `from app import sdk` vira "app.sdk": cabe em allow=["app.sdk"]
                and not any(_under(mod, a) for a in only["allow"])
            ):
                out.append(Violation(rule["name"], rel, line, mod))
    return out


def ts_imports(source: str) -> list[tuple[int, str]]:
    return [(source.count("\n", 0, m.start()) + 1, m.group(1)) for m in _TS_IMPORT.finditer(source)]


def _check_typescript(root: Path, rule: dict) -> list[Violation]:
    out: list[Violation] = []
    forbid_dirs = [(root / d).resolve() for d in rule.get("forbid_dirs", [])]
    own_dirs = [(root / p).resolve() for p in rule["paths"]]
    forbid_pkgs = rule.get("forbid_packages", [])
    entry = rule.get("public_entry")
    for f in _files(root, rule["paths"], (".ts", ".tsx")):
        rel = str(f.relative_to(root))
        for line, spec in ts_imports(f.read_text(encoding="utf-8")):
            if spec.startswith("."):
                target = (f.parent / spec).resolve()
                inside_own = any(target.is_relative_to(d) for d in own_dirs)
                if not inside_own and any(target.is_relative_to(d) for d in forbid_dirs):
                    out.append(Violation(rule["name"], rel, line, spec))
                continue
            if any(spec == p or spec.startswith(p + "/") for p in forbid_pkgs):
                out.append(Violation(rule["name"], rel, line, spec))
            elif entry and spec.startswith(entry["package"] + "/"):
                sub = spec[len(entry["package"]) + 1 :]
                if sub not in entry.get("allow_subpaths", []):
                    out.append(Violation(rule["name"], rel, line, spec))
    return out


def boundary_violations(root: Path, policy: Path = POLICY) -> list[Violation]:
    data = tomllib.loads((root / policy).read_text(encoding="utf-8"))
    out: list[Violation] = []
    for rule in data.get("python", []):
        out += _check_python(root, rule)
    for rule in data.get("typescript", []):
        out += _check_typescript(root, rule)
    return out
