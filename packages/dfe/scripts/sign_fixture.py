"""Assina fixtures do DF-e com chave EFEMERA e certificado autoassinado
ficticio (P9/P7). A chave privada nao e' gravada em lugar nenhum.

    PYTHONPATH=src:../.. uv run --project ../.. python scripts/sign_fixture.py positivo
    PYTHONPATH=src:../.. uv run --project ../.. python scripts/sign_fixture.py variantes

`positivo` reassina o controle positivo; `variantes` gera, a partir dele, as
NF-e assinadas com certificado de outro CNPJ e com certificado vencido antes
do dhEmi (casos F-sigcnpj e F-sigvalid do dataset).
"""

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "src")]

from dfe_copilot.fiscal.xml_tools import NS, safe_parse
from lxml import etree
from signing import make_cert, sign

FIXTURES = ROOT / "tests" / "fixtures"
POSITIVO = FIXTURES / "nfe_controle_positivo_sp.xml"
VARIANTES = {
    "nfe_assinada_cnpj_divergente.xml": {"cnpj": "99888777000166"},
    "nfe_assinada_cert_vencido.xml": {
        "not_before": dt.datetime(2016, 1, 1, tzinfo=dt.UTC),
        "not_after": dt.datetime(2017, 1, 1, tzinfo=dt.UTC),
    },
}


def _assinar(src: Path, dst: Path, **cert_kw) -> None:
    root = safe_parse(src.read_bytes())
    key, cert = make_cert(**cert_kw)
    sign(root, root.find(f"{{{NS}}}infNFe"), key, cert)
    tree = root.getroottree()  # preserva o comentario de procedencia
    dst.write_bytes(etree.tostring(tree, encoding="UTF-8", xml_declaration=True) + b"\n")
    print(f"assinado: {dst.name}")


def main() -> None:
    alvo = sys.argv[1] if len(sys.argv) > 1 else ""
    if alvo == "positivo":
        _assinar(POSITIVO, POSITIVO)
    elif alvo == "variantes":
        for nome, kw in VARIANTES.items():
            _assinar(POSITIVO, FIXTURES / nome, **kw)
    else:
        raise SystemExit("uso: sign_fixture.py positivo|variantes")


if __name__ == "__main__":
    main()
