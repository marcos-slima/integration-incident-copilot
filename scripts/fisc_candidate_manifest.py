"""Record exact candidate artifacts and source inputs without importing the app."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def constants(path: Path) -> dict:
    return {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id.endswith("_CONTRACT_VERSION")
    }


def manifest(root: Path, directory: Path) -> dict:
    versions = {
        "dfe-copilot": tomllib.loads((root / "packages/dfe/pyproject.toml").read_text())["project"][
            "version"
        ],
        "copilot-core": tomllib.loads((root / "packages/core/pyproject.toml").read_text())[
            "project"
        ]["version"],
    }
    found = {}
    for wheel in directory.glob("*.whl"):
        with zipfile.ZipFile(wheel) as archive:
            metadata = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(metadata) != 1:
                raise ValueError(f"Wheel metadata ambiguous: {wheel.name}")
            message = Parser().parsestr(archive.read(metadata[0]).decode())
            name, version = message["Name"], message["Version"]
            if name not in versions or version != versions[name] or name in found:
                raise ValueError(f"Unexpected wheel/version: {wheel.name}")
            found[name] = version
    if found != versions:
        raise ValueError("Both fiscal and core wheels are required")
    paths = [
        "uv.lock",
        "package-lock.json",
        "packages/dfe/data/eval/prompt_baseline.json",
        "packages/dfe/data/corpus/manifest.json",
        "packages/dfe/data/schemas/nfe/PL_010g_v1.03.manifest.json",
    ]
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    payload = {
        "status": "candidate-not-a-release",
        "product": "FISC",
        "versions": versions,
        "core_ui_version": json.loads((root / "packages/core-ui/package.json").read_text())[
            "version"
        ],
        "contracts": constants(root / "packages/core/src/app/contracts/versioning.py"),
        "source_commit": sha,
        "ci_event": os.environ.get("GITHUB_EVENT_NAME"),
        "ci_run_id": os.environ.get("GITHUB_RUN_ID"),
        "ci_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "input_sha256": {path: sha256(root / path) for path in paths},
        "artifact_sha256": {
            p.name: sha256(p)
            for p in sorted(directory.iterdir())
            if p.is_file() and p.suffix != ".json" and p.name != "SHA256SUMS"
        },
        "limitations": [
            "synthetic fiscal validation; no SAP/SEFAZ certification",
            "PRELOAD_MODELS=0; runtime may need model downloads",
        ],
    }
    if (directory / "dfe-image.tar.gz").exists():
        payload["docker_image_id"] = subprocess.check_output(
            ["docker", "image", "inspect", "dfe-copilot:ci", "--format", "{{.Id}}"],
            text=True,
        ).strip()
    return payload


def main() -> None:
    directory = Path(sys.argv[1])
    payload = manifest(ROOT, directory)
    (directory / "candidate.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    files = sorted(p for p in directory.iterdir() if p.is_file() and p.name != "SHA256SUMS")
    (directory / "SHA256SUMS").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in files))
    print(f"FISC {payload['versions']['dfe-copilot']} candidate from {payload['source_commit']}")


if __name__ == "__main__":
    main()
