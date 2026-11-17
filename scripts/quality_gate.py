"""IIC consumer gate: platform implementation/gates are owned upstream."""

import ast
from pathlib import Path

root = Path(__file__).resolve().parents[1]
assert not (root / "app").exists(), "Do not vendor platform source"
assert not (root / "packages/core").exists()
for file in (root / "iic_copilot").glob("*.py"):
    for node in ast.walk(ast.parse(file.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app"):
            assert node.module.startswith("app.sdk"), (file, node.module)
text = (root / "frontend/package.json").read_text()
assert "copilot-platform/releases/" in text
print("IIC consumer boundaries and dependency channel verified")
