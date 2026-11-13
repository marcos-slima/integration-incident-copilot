"""Diretórios externos de operação e de ferramentas do monorepo."""

import os
from pathlib import Path


def project_root() -> Path:
    override = os.environ.get("COPILOT_HOME")
    if override:
        return Path(override).resolve()
    for parent in Path(__file__).resolve().parents:
        if (parent / "architecture" / "boundaries.toml").is_file():
            return parent
    return Path.cwd().resolve()
