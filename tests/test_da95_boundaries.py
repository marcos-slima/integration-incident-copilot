"""DA-95: gate de fronteiras e i18n das superficies de UI."""

from __future__ import annotations

import shutil
from pathlib import Path

from app.evaluation.boundaries import POLICY, boundary_violations, python_imports, ts_imports
from app.evaluation.gates import check_import_boundaries
from app.i18n.checks import ui_i18n_problems, web_surfaces

ROOT = Path(__file__).resolve().parents[1]


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "architecture").mkdir()
    shutil.copy(ROOT / POLICY, tmp_path / POLICY)
    return tmp_path


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_repositorio_respeita_as_fronteiras() -> None:
    assert boundary_violations(ROOT) == []
    assert not any(f.is_failure for f in check_import_boundaries(ROOT))


def test_python_imports_inclui_import_tardio() -> None:
    src = "import os\nfrom app import sdk\n\ndef f():\n    from app.rag.retriever import x\n"
    assert python_imports(src) == [(1, "os"), (2, "app.sdk"), (5, "app.rag.retriever.x")]


def test_core_importando_produto_reprova(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _write(root, "app/x.py", "import dfe_copilot.plugin\n")
    (v,) = boundary_violations(root)
    assert v.rule == "core nao importa produto" and v.target == "dfe_copilot.plugin"


def test_produto_fora_do_sdk_reprova(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _write(
        root,
        "packages/dfe/src/dfe_copilot/a.py",
        "from app import sdk\nfrom app.sdk.testing import h\n",
    )
    assert boundary_violations(root) == []
    _write(
        root,
        "packages/dfe/src/dfe_copilot/b.py",
        "from app.rag.retriever import _get_qdrant_client\n",
    )
    (v,) = boundary_violations(root)
    assert v.file.endswith("b.py") and v.target.startswith("app.rag.retriever")


def test_ts_import_profundo_e_cruzado_reprova(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _write(
        root,
        "packages/dfe/frontend/src/ok.tsx",
        "import { X } from '@copilot/core-ui';\nimport '@copilot/core-ui/styles.css';\nimport { Y } from './y';\n",
    )
    assert boundary_violations(root) == []
    _write(
        root,
        "packages/dfe/frontend/src/deep.ts",
        "import { A } from '@copilot/core-ui/src/api/http';\n",
    )
    _write(
        root,
        "packages/dfe/frontend/src/cross.ts",
        "import { B } from '../../../../frontend/src/App';\n",
    )
    _write(root, "packages/core-ui/src/leak.ts", "import { C } from '@copilot/dfe-ui';\n")
    found = {(v.file.rsplit("/", 1)[-1], v.target) for v in boundary_violations(root)}
    assert found == {
        ("deep.ts", "@copilot/core-ui/src/api/http"),
        ("cross.ts", "../../../../frontend/src/App"),
        ("leak.ts", "@copilot/dfe-ui"),
    }


def test_ts_imports_tipos_e_dinamico() -> None:
    src = "import type { A } from 'a';\nconst m = import('b');\nimport './c.css';\n"
    assert [s for _, s in ts_imports(src)] == ["a", "b", "./c.css"]


def test_superficies_de_ui_descobertas() -> None:
    assert {str(p) for p in web_surfaces(ROOT)} == {
        "frontend/src",
        "packages/core-ui/src",
        "packages/dfe/frontend/src",
    }


def test_i18n_reprova_chave_ausente_e_core_no_produto(tmp_path: Path) -> None:
    for rel in ("app/i18n/locales", "app/admin/templates", "app/admin/static"):
        shutil.copytree(ROOT / rel, tmp_path / rel)
    shutil.copytree(ROOT / "packages/core-ui/src/i18n", tmp_path / "packages/core-ui/src/i18n")
    loc = tmp_path / "packages/x/frontend/src/i18n/locales"
    loc.mkdir(parents=True)
    for name in ("pt-BR", "en"):
        (loc / f"{name}.json").write_text('{"a": {"b": "x"}, "core": {"c": "y"}}', encoding="utf-8")
    _write(
        tmp_path,
        "packages/x/frontend/src/V.tsx",
        "t('a.b'); t('a.zz'); t('core.login.title'); t('core.form.add');\n",
    )
    problems = ui_i18n_problems(tmp_path)
    assert any("'core.*'" in p for p in problems)
    assert any("'a.zz'" in p for p in problems)
    assert any("'core.login.title'" in p for p in problems)
    assert not any("'a.b'" in p or "'core.form.add'" in p for p in problems)
