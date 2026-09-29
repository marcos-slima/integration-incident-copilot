"""DA-52: roda uma observacao de drift pela linha de comando.

    python scripts/check_contract_drift.py --system-key sap_odata_prod --connector-type odata
    python scripts/check_contract_drift.py --system-key sap_odata_prod --connector-type odata --json
    python scripts/check_contract_drift.py --system-key sap_odata_prod --connector-type odata --no-emit

Codigos de saida (pensados para o CI, nao so para o olho):

    0  clean, first_observation ou unverified   -- nada a fazer
    1  drift breaking detectado                 -- o contrato quebrou compatibilidade
    2  erro de uso (falta --system-key, connector desconhecido)

`unverified` sai 0 de proposito. Um SAP fora do ar nao e' motivo para
marcar o build como vermelho: o detector nao tem opiniao, e code de
saida de erro transformaria "nao deu para checar" em "deu errado" --
mesma confusao do preflight de RAM, resolvida na outra direcao (la falha
de medicao nao bloqueia o trabalho; aqui falha de medicao nao gera
alarme). Quem precisa distinguir os tres le `--json` ou o texto.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from app.contracts.diff import ObservationStatus
from app.contracts.observe import check_connector, describe_report, emit_incident

EXIT_CLEAN = 0
EXIT_BREAKING = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_contract_drift",
        description="Observa o contrato publicado por um sistema integrado e compara com o baseline.",
    )
    parser.add_argument(
        "--system-key",
        required=True,
        help=(
            "system_key do catalogo (integration_systems, DA-49). E' a identidade "
            "do baseline E o que o incidente de drift vai usar para correlacionar "
            "(DA-50). Nao use um rotulo livre: 'OData' nao casa com o catalogo."
        ),
    )
    parser.add_argument(
        "--connector-type",
        required=True,
        help="Literal do pipeline: odata, rfc, servicenow, ...",
    )
    parser.add_argument(
        "--no-emit",
        action="store_true",
        help="Observa e imprime o report, mas nao abre incidente (breaking fica so no relatorio).",
    )
    parser.add_argument("--json", action="store_true", help="Saida em JSON (o relatorio inteiro).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from app.connectors import get_connector

    try:
        connector = get_connector(args.connector_type)
    except (ValueError, KeyError):
        # `get_connector` levanta ValueError (nao KeyError) para tipo
        # desconhecido. Traceback num CLI e' ruido: o operador passou o
        # argumento errado e precisa da lista do que existe.
        from app.connectors import _REGISTRY

        print(
            f"connector_type desconhecido: {args.connector_type!r}. "
            f"Validos: {', '.join(sorted(_REGISTRY))}",
            file=sys.stderr,
        )
        return EXIT_USAGE

    report = check_connector(
        connector,
        system_key=args.system_key,
        connector_type=args.connector_type,
    )
    emitted = False
    if report.is_breaking and not args.no_emit:
        emitted = emit_incident(report, connector_type=args.connector_type)

    if args.json:
        payload: dict[str, Any] = dict(report.as_dict())
        payload["incident_emitted"] = emitted
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(describe_report(report))
        for change in report.changes:
            print(f"  - [{change.severity}] {change.describe()}")
        if report.fingerprint_before:
            print(f"  baseline: {report.fingerprint_before[:16]}")
        print(f"  atual:    {(report.fingerprint_after or '-')[:16]}")
        if report.is_breaking:
            print("  incidente: emitido" if emitted else "  incidente: NAO emitido (--no-emit?)")
        if report.status is ObservationStatus.UNVERIFIED:
            print("  (sem opiniao: o contrato nao pode ser verificado)")

    return EXIT_BREAKING if report.is_breaking else EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
