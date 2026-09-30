"""DA-58: gera o mapa de cobertura produto SAP x mecanismo.

    python scripts/coverage_map.py                # imprime o mapa
    python scripts/coverage_map.py --gaps         # so' a fila de trabalho
    python scripts/coverage_map.py --write        # regrava docs/COVERAGE_MAP.md
    python scripts/coverage_map.py --check        # CI: reprova se desatualizado

Codigos de saida:

    0  mapa coerente (e, no --check, o doc versionado esta em dia)
    1  dado incoerente: conector registrado sem declaracao de cobertura,
       produto fantasma, mecanismo fora das dimensoes, ou doc desatualizado
    2  erro de uso

Reprovar por INCOERENCIA e' deliberado. Reprovar por lacuna nao seria:
exigir cobertura completa seria exigir 76 conectores novos para o CI ficar
verde, e o gate deixaria de medir a unica coisa que importa — se o mapa
ainda corresponde ao codigo. A lacuna e' o relatorio; a incoerencia e' o
defeito.

E' o gate que roda com a suite (DA-51): `--check` e' o que o
`scripts/quality_gate.py` chama, entao nao ha uma segunda resposta sobre
o estado do mapa.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.evaluation.coverage import (
    GENERATED_NOTE,
    MAP_DOC,
    CoverageError,
    build_matrix,
    check_consistency,
    gaps_summary,
    load_all,
    render_markdown,
)
from app.evaluation.gates import registered_connectors

EXIT_OK = 0
EXIT_INCOHERENT = 1
EXIT_USAGE = 2


def _print_gaps(linhas, labels) -> None:
    gaps = gaps_summary(linhas)
    if not gaps:
        print("nenhuma lacuna: todo mecanismo declarado tem cobertura")
        return
    print(f"{len(gaps)} mecanismo(s) com lacuna, por peso:\n")
    for mecanismo, count in gaps.items():
        print(f"  {labels.get(mecanismo, mecanismo):<20} {count} produto(s) sem cobertura")
    print("\npor produto:")
    for linha in linhas:
        if not linha.lacunas:
            continue
        dedicados = ", ".join(linha.conectores_dedicados) or "—"
        faltando = ", ".join(labels.get(m, m) for m in linha.lacunas)
        print(f"  {linha.product.name:<30} dedicado={dedicados:<18} falta: {faltando}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="coverage_map",
        description="Mapa de cobertura produto SAP x mecanismo, calculado dos dados versionados.",
    )
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument(
        "--write", action="store_true", help="regrava o mapa em docs/COVERAGE_MAP.md"
    )
    grupo.add_argument(
        "--check", action="store_true", help="verifica coerencia e se o doc esta em dia (CI)"
    )
    grupo.add_argument("--gaps", action="store_true", help="imprime so a fila de trabalho")
    args = parser.parse_args(argv)

    try:
        produtos, mecanismos, cobertura, labels = load_all()
        conectores = registered_connectors(REPO_ROOT)
    except CoverageError as exc:
        print(f"erro de dado: {exc}", file=sys.stderr)
        return EXIT_INCOHERENT

    problemas = check_consistency(conectores, produtos, cobertura)
    if problemas:
        print("mapa INCOERENTE com o codigo:", file=sys.stderr)
        for problema in problemas:
            print(f"  - {problema}", file=sys.stderr)
        return EXIT_INCOHERENT

    linhas = build_matrix(produtos, cobertura, mecanismos)

    if args.gaps:
        _print_gaps(linhas, labels)
        return EXIT_OK

    mapa = render_markdown(linhas, mecanismos, labels, GENERATED_NOTE)

    if args.write:
        destino = REPO_ROOT / MAP_DOC
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(mapa, encoding="utf-8")
        total_gaps = sum(len(l.lacunas) for l in linhas)
        print(f"mapa gravado em {MAP_DOC} ({len(linhas)} linhas, {total_gaps} lacunas)")
        return EXIT_OK

    if args.check:
        destino = REPO_ROOT / MAP_DOC
        if not destino.exists():
            print(
                f"{MAP_DOC} nao existe — rode `python scripts/coverage_map.py --write`",
                file=sys.stderr,
            )
            return EXIT_INCOHERENT
        atual = destino.read_text(encoding="utf-8")
        if atual != mapa:
            print(
                f"{MAP_DOC} esta desatualizado em relacao aos dados.\n"
                "Rode `python scripts/coverage_map.py --write` e commite o resultado.\n"
                "Se voce NAO mexeu nos dados, o problema e' o doc — nao edite a mao:\n"
                "ele e' gerado, e editar aqui seria desfazer a proxima regeneracao.",
                file=sys.stderr,
            )
            return EXIT_INCOHERENT
        print(f"{MAP_DOC} em dia com os dados")
        return EXIT_OK

    sys.stdout.write(mapa)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
