"""Recursos do produto em desenvolvimento ou no wheel instalado."""

from pathlib import Path

_PACKAGE = Path(__file__).resolve().parent
_SOURCE = _PACKAGE.parents[1]
DATA_DIR = _PACKAGE / "_data" if (_PACKAGE / "_data").is_dir() else _SOURCE / "data"
UI_DIR = _PACKAGE / "_ui" if (_PACKAGE / "_ui").is_dir() else _SOURCE / "frontend" / "dist"
