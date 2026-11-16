"""Build portable libraries and an allowlisted operations bundle; never publish."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def operation_files(root: Path) -> list[Path]:
    paths = [root / "alembic.ini", root / "LICENSE", root / "scripts/validate_dashboards.py"]
    paths.extend(root.glob("alembic/*.py"))
    paths.extend(root.glob("alembic/*.mako"))
    paths.extend(root.glob("alembic/versions/*.py"))
    paths.extend(root.glob("deploy/grafana/dashboards/*.json"))
    if not list(root.glob("alembic/versions/*.py")):
        raise ValueError("Operations require migration revisions")
    return sorted(paths)


def operations_bundle(root: Path, out: Path, version: str, commit: str) -> Path:
    paths = operation_files(root)
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Unexpected operational resource: {path}")
    metadata = {
        "core_version": version,
        "source_commit": commit,
        "files_sha256": {p.relative_to(root).as_posix(): digest(p) for p in paths},
    }
    target = out / f"copilot-core-operations-{version}.tar.gz"
    with tarfile.open(target, "w:gz") as archive:
        for path in paths:
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Unexpected operational resource: {path}")
            archive.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
        for name, data in {
            "MANIFEST.json": json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            "README.md": (root / "packages/core/docs/OPERATIONS.md").read_text(),
        }.items():
            content = data.encode()
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(content), 0o644
            archive.addfile(info, io.BytesIO(content))
    return target


def build(root: Path, out: Path) -> None:
    if out.exists() and any(p.name != ".gitignore" for p in out.iterdir()):
        raise ValueError("Use an empty output directory to avoid mixing versions")
    out.mkdir(parents=True, exist_ok=True)
    version = tomllib.loads((root / "packages/core/pyproject.toml").read_text())["project"][
        "version"
    ]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    # Build from the core alone: root workspace/product files cannot hide missing resources.
    with tempfile.TemporaryDirectory(prefix="copilot-library-source-") as directory:
        source = Path(directory) / "core"
        shutil.copytree(
            root / "packages/core",
            source,
            ignore=shutil.ignore_patterns("dist", "__pycache__", ".venv", "*.egg-info"),
        )
        subprocess.run(
            ["uv", "build", "--no-sources", "--out-dir", str(out)], cwd=source, check=True
        )
    subprocess.run(["npm", "run", "build", "-w", "@copilot/core-ui"], cwd=root, check=True)
    subprocess.run(
        [
            "npm",
            "pack",
            "-w",
            "@copilot/core-ui",
            "--ignore-scripts",
            "--pack-destination",
            str(out),
        ],
        cwd=root,
        check=True,
    )
    operations_bundle(root, out, version, commit)
    # uv creates this marker even when an earlier build fails; it is not an artifact.
    (out / ".gitignore").unlink(missing_ok=True)
    metadata = {
        "status": "distribution-candidate-not-published",
        "source_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True)
        ),
        "source_commit": commit,
        "core_version": version,
        "core_ui_version": json.loads((root / "packages/core-ui/package.json").read_text())[
            "version"
        ],
        "channels": {
            "python": "wheel/sdist assets",
            "ui": "npm tarball asset",
            "operations": "tar.gz asset",
        },
        "files_sha256": {p.name: digest(p) for p in sorted(out.iterdir()) if p.is_file()},
    }
    (out / "distribution.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    (out / "SHA256SUMS").write_text(
        "".join(
            f"{digest(p)}  {p.name}\n"
            for p in sorted(out.iterdir())
            if p.is_file() and p.name != "SHA256SUMS"
        )
    )
    print(f"Distribution candidate: {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    build(ROOT, args.out_dir.resolve())
