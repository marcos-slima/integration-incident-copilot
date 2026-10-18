"""DA-73: lista texto visivel fora dos catalogos (`--check` sai com 1 se houver).

uv run python scripts/i18n_inventory.py            # inventario por arquivo
uv run python scripts/i18n_inventory.py --check    # para CI / quality gate
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.i18n.inventory import scan_repo


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    findings = scan_repo(root)
    for f in findings:
        print(f)
    per_file = Counter(f.path for f in findings)
    print(f"\n{len(findings)} texto(s) fora do catalogo em {len(per_file)} arquivo(s)")
    for path, n in per_file.most_common():
        print(f"  {n:4d}  {path}")
    return 1 if (args.check and findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
