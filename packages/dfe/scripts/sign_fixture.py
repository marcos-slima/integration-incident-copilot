"""Reassina o controle positivo com chave EFEMERA e certificado autoassinado
ficticio (P9). A chave privada nao e' gravada em lugar nenhum.

    PYTHONPATH=src:../.. uv run --project ../.. python scripts/sign_fixture.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "src")]

from dfe_copilot.fiscal.xml_tools import NS, safe_parse
from lxml import etree
from signing import make_cert, sign

FIXTURE = ROOT / "tests" / "fixtures" / "nfe_controle_positivo_sp.xml"


def main() -> None:
    raw = FIXTURE.read_bytes()
    root = safe_parse(raw)
    key, cert = make_cert()
    sign(root, root.find(f"{{{NS}}}infNFe"), key, cert)
    tree = root.getroottree()  # preserva o comentario de procedencia
    FIXTURE.write_bytes(etree.tostring(tree, encoding="UTF-8", xml_declaration=True) + b"\n")
    print(f"reassinado: {FIXTURE}")


if __name__ == "__main__":
    main()
