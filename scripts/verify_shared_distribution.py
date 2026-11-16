"""Inspect portable artifacts and smoke-test the installed core outside the checkout."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


def verify(directory: Path) -> tuple[Path, Path]:
    checksum_lines = (directory / "SHA256SUMS").read_text().splitlines()
    checked = set()
    for line in checksum_lines:
        expected, name = line.split("  ", 1)
        if Path(name).name != name or name in checked:
            raise ValueError("Unsafe or duplicate checksum entry")
        checked.add(name)
        with (directory / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError(f"Checksum mismatch: {name}")
    if checked != {p.name for p in directory.iterdir() if p.is_file() and p.name != "SHA256SUMS"}:
        raise ValueError("Incomplete checksum inventory")
    metadata = json.loads((directory / "distribution.json").read_text())
    version = metadata["core_version"]
    (wheel,) = directory.glob("copilot_core-*.whl")
    sdist = directory / f"copilot_core-{version}.tar.gz"
    operations = directory / f"copilot-core-operations-{version}.tar.gz"
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        required = {
            "app/sdk/__init__.py",
            "app/ontology/error_codes.ttl",
            "app/_data/skills/diagnose_incident.yaml",
        }
        if not required <= names or not any(n.startswith("app/admin/templates/") for n in names):
            raise ValueError("Missing core runtime resources")
        if not any(n.endswith("/licenses/LICENSE") for n in names):
            raise ValueError("Missing Python license")
        if any(not (n.startswith("app/") or ".dist-info/" in n) for n in names):
            raise ValueError("Unexpected namespace in core wheel")
    with tarfile.open(sdist) as archive:
        if not any(n.endswith("/README.md") for n in archive.getnames()):
            raise ValueError("Missing source README")
    with tarfile.open(operations) as archive:
        members = archive.getmembers()
        if any(
            not m.isfile()
            or PurePosixPath(m.name).is_absolute()
            or ".." in PurePosixPath(m.name).parts
            for m in members
        ):
            raise ValueError("Unsafe operations archive")
        manifest = json.load(archive.extractfile("MANIFEST.json"))
        if (
            manifest["core_version"] != version
            or manifest["source_commit"] != metadata["source_commit"]
        ):
            raise ValueError("Operations/core provenance mismatch")
        if set(archive.getnames()) != set(manifest["files_sha256"]) | {
            "MANIFEST.json",
            "README.md",
        }:
            raise ValueError("Unexpected operations resources")
        for name, expected in manifest["files_sha256"].items():
            if hashlib.sha256(archive.extractfile(name).read()).hexdigest() != expected:
                raise ValueError(f"Operations checksum mismatch: {name}")
    (ui,) = directory.glob("copilot-core-ui-*.tgz")
    with tarfile.open(ui) as archive:
        package = json.load(archive.extractfile("package/package.json"))
        required = {
            "package/dist/index.js",
            "package/dist/index.d.ts",
            "package/dist/styles.css",
            "package/LICENSE",
            "package/README.md",
        }
        if not required <= set(archive.getnames()):
            raise ValueError("Missing UI distribution resources")
        if (
            package["name"] != "@copilot/core-ui"
            or package["version"] != metadata["core_ui_version"]
        ):
            raise ValueError("UI identity/version mismatch")
        if any(n.startswith("package/src/") for n in archive.getnames()):
            raise ValueError("UI artifact contains private source entries")
    return wheel, operations


def smoke(wheel: Path, operations: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="copilot-installed-distribution-") as temporary:
        root = Path(temporary)
        installed = root / "site"
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                sys.executable,
                "--no-deps",
                "--target",
                str(installed),
                str(wheel.resolve()),
            ],
            check=True,
        )
        with tarfile.open(operations) as archive:
            archive.extractall(root / "operations", filter="data")
        code = """
import importlib.abc, pathlib, sys, os
sys.path.insert(0, sys.argv[1])
class NoProducts(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'dfe_copilot', 'iic_copilot'}:
            raise ImportError('Product import from shared core: ' + fullname)
sys.meta_path.insert(0, NoProducts())
import app
assert pathlib.Path(app.__file__).resolve().is_relative_to(pathlib.Path(sys.argv[1]).resolve())
from app import sdk
assert sdk.asgi_app() is not None
assert sdk.plugin_catalog() == []
from alembic.config import Config
from alembic.script import ScriptDirectory
scripts = ScriptDirectory.from_config(Config(str(pathlib.Path(sys.argv[2]) / 'alembic.ini')))
assert len(scripts.get_heads()) == 1
assert list(scripts.walk_revisions())
print('Installed core and operations load without product sources; migration graph has one head')
"""
        env = {
            **os.environ,
            "DOMAIN_PLUGINS": "",
            "DATABASE_URL": "",
            "REDIS_URL": "",
            "GRAPH_RAG_ENABLED": "false",
            "CHECKPOINTING_ENABLED": "false",
            "AMQP_ENABLED": "false",
        }
        subprocess.run(
            [sys.executable, "-I", "-c", code, str(installed), str(root / "operations")],
            cwd=root,
            env=env,
            check=True,
        )


if __name__ == "__main__":
    wheel, operations = verify(Path(sys.argv[1]))
    smoke(wheel, operations)
