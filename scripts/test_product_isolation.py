"""DA-95: testa o DF-e contra o wheel do core, sem fontes do IIC.

Usa dependências já sincronizadas no ambiente do runner; não publica pacotes.
Execute com `uv run python scripts/test_product_isolation.py`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="copilot-isolation-") as scratch:
        work = Path(scratch)
        wheels = work / "wheels"
        core_source = work / "core-source"
        shutil.copytree(
            ROOT / "packages" / "core",
            core_source,
            ignore=shutil.ignore_patterns("__pycache__", "dist", ".venv", ".ruff_cache"),
        )
        subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(wheels)], cwd=core_source, check=True
        )
        wheel = next(wheels.glob("*.whl"))
        core = work / "installed"
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                sys.executable,
                "--no-deps",
                "--target",
                str(core),
                str(wheel),
            ],
            check=True,
        )
        smoke = """
import importlib.abc, pathlib, sys, os
sys.path.insert(0, sys.argv[1])
os.environ['DOMAIN_PLUGINS'] = ''
class NoProducts(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'dfe_copilot', 'iic_copilot'}:
            raise ImportError('Core tentou importar produto: ' + fullname)
sys.meta_path.insert(0, NoProducts())
import app.sdk.pytest_plugin
from app import sdk
assert sdk.asgi_app() is not None
assert sdk.plugin_catalog() == []
assert not any(name.split('.')[0] in {'dfe_copilot', 'iic_copilot'} for name in sys.modules)
print('Core inicializa sem imports de produtos', flush=True)
"""
        subprocess.run([sys.executable, "-I", "-c", smoke, str(core)], cwd=core_source, check=True)
        product = work / "product"
        source = ROOT / "packages" / "dfe"
        for directory in ("src", "tests", "data", "scripts", "docs"):
            shutil.copytree(
                source / directory,
                product / directory,
                ignore=shutil.ignore_patterns("__pycache__", "MOC_7.0_Anexo_I.pdf"),
            )
        shutil.copy2(source / "pyproject.toml", product / "pyproject.toml")
        shutil.copy2(source / "hatch_build.py", product / "hatch_build.py")
        for config in source.glob("promptfooconfig*.yaml"):
            shutil.copy2(config, product / config.name)
        product_wheels = work / "product-wheels"
        subprocess.run(
            ["uv", "build", "--no-sources", "--wheel", "--out-dir", str(product_wheels)],
            cwd=product,
            env={**os.environ, "DFE_BUILD_REQUIRE_UI": "0"},
            check=True,
        )
        required_ui = subprocess.run(
            ["uv", "build", "--no-sources", "--wheel", "--out-dir", str(product_wheels)],
            cwd=product,
            env={**os.environ, "DFE_BUILD_REQUIRE_UI": "1"},
            capture_output=True,
            text=True,
            check=False,
        )
        assert required_ui.returncode != 0, "Release sem UI foi aceito"
        assert "Build DF-e exige a UI" in required_ui.stderr
        print("Build de entrega sem UI recusado corretamente", flush=True)

        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                sys.executable,
                "--no-deps",
                "--target",
                str(core),
                str(next(product_wheels.glob("*.whl"))),
            ],
            check=True,
        )
        # -I exclui cwd e PYTHONPATH do processo pai. O namespace app deve vir
        # exclusivamente do wheel instalado, nunca de um editable do checkout.
        code = """
import pathlib, sys
core = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(core))
import app
assert pathlib.Path(app.__file__).resolve().is_relative_to(core), app.__file__
from app.mcp.skill_registry import DEFAULT_SKILLS_DIR
assert DEFAULT_SKILLS_DIR.is_relative_to(core), DEFAULT_SKILLS_DIR
assert DEFAULT_SKILLS_DIR.is_dir(), DEFAULT_SKILLS_DIR
print('Core instalado do wheel:', app.__file__, flush=True)
import dfe_copilot
assert pathlib.Path(dfe_copilot.__file__).resolve().is_relative_to(core), dfe_copilot.__file__
print('DF-e instalado do wheel:', dfe_copilot.__file__, flush=True)
import pytest
raise SystemExit(pytest.main(['-c', 'pyproject.toml', '-o', 'pythonpath=', 'tests', '-q', '-p', 'no:cacheprovider']))
"""
        subprocess.run([sys.executable, "-I", "-c", code, str(core)], cwd=product, check=True)


if __name__ == "__main__":
    main()
