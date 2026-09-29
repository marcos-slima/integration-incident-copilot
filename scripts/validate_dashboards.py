"""Valida o SQL de todos os dashboards Grafana contra o Postgres real (DA-50).

Problema que motivou o script: dashboards sao JSON provisionado, entao erro de
SQL so aparece como painel vermelho no Grafana. Ja aconteceu duas vezes --
`evidence_strength IN ('high','critical')` numa coluna FLOAT (migration 002) e
`ROUND(<double>, 1)` (round(double, int) nao existe no Postgres). Ambos
quebravam a query inteira, nao so o painel.

O que o script faz:
  1. le `deploy/grafana/dashboards/*.json`;
  2. extrai todo `rawSql` dos targets;
  3. substitui as macros do Grafana ($__timeFilter, $__timeGroupAlias,
     $__timeFrom) e as variaveis de template do dashboard ($var, 'All') por
     literais SQL;
  4. executa cada query no Postgres e reporta erro/ok.

Somente SELECT: o script nunca escreve no banco. Cada query e executada dentro
de uma transacao que sofre ROLLBACK, para que um dashboard com efeito colateral
(nao deveria existir) nao persista.

Uso:
    # com DATABASE_URL apontando para o Postgres de desenvolvimento
    uv run python scripts/validate_dashboards.py

    # ou via container, sem depender do .env local
    uv run python scripts/validate_dashboards.py --psql \
        --container integration-incident-copilot-postgres-1 \
        --user iic --dbname iic
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

DASHBOARD_DIR = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "grafana" / "dashboards"

_INTERVAL = {
    "1h": "hour",
    "6h": "6 hours",
    "12h": "12 hours",
    "1d": "day",
    "1w": "week",
}

# O nome completo da unidade em date_trunc. Sem este mapa, o fallback
# geraria "30 ms" para '30m' (milisegundos) e a query passaria a agrupar
# pela unidade errada em vez de falhar.
_UNIT_NAMES = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}


def _time_group_literal(interval: str) -> str:
    """'1h' -> 'hour': date_trunc no Postgres aceita texto, nao INTERVAL.

    O Grafana aceita o shorthand '1h' e monta o INTERVAL sozinho; aqui a
    query roda direto no psql, entao precisa virar o argumento textual.
    """
    key = interval.strip().strip("'").lower()
    if key in _INTERVAL:
        return _INTERVAL[key]
    m = re.fullmatch(r"(\d+)\s*([smhdw])", key)
    if not m:
        return interval.strip("'")
    value, unit = m.groups()
    return f"{value} {_UNIT_NAMES[unit]}" if value != "1" else _UNIT_NAMES[unit]


def normalize_sql(sql: str, variables: list[str]) -> str:
    """Troca macros/vars do Grafana por literais que o Postgres entende."""
    out = sql
    for var in variables:
        # ($var = 'All' OR col = ANY(string_to_array('$var', ',')))
        out = re.sub(
            r"\(\s*" + re.escape(f"${var}") + r"\s*=\s*'[^']*'",
            "(TRUE",
            out,
        )
        out = out.replace(f"'${var}'", "''")
        out = re.sub(re.escape(f"${{{var}}}") + r"|" + re.escape(f"${var}") + r"\b", "", out)
    out = re.sub(
        r"\$__timeGroupAlias\(\s*([\w.\"]+)\s*,\s*'([^']+)'\s*\)",
        lambda m: f"date_trunc('{_time_group_literal(m.group(2))}', {m.group(1)})",
        out,
    )
    out = re.sub(
        r"\$__timeGroupAlias\(\s*([\w.\"]+)\s*,\s*\$__interval\s*\)",
        r"date_trunc('hour', \1)",
        out,
    )
    out = re.sub(
        r"\$__timeFilter\(\s*([\w.\"]+)\s*\)",
        r"(\1 >= NOW() - INTERVAL '7 days' AND \1 <= NOW())",
        out,
    )
    out = re.sub(r"\$__timeFrom\(\)\s*::\s*\w+", "(NOW() - INTERVAL '7 days')", out)
    out = out.replace("$__interval", "INTERVAL '1 hour'")
    return out


def _remaining_macros(sql: str) -> list[str]:
    return sorted(set(re.findall(r"\$[A-Za-z_][A-Za-z0-9_]*", sql)))


def _vars_of(dashboard: dict) -> list[str]:
    return [v["name"] for v in dashboard.get("templating", {}).get("list", []) if "name" in v]


def _panels(dashboard: dict):
    for panel in dashboard.get("panels", []):
        for target in panel.get("targets", []) or []:
            if "rawSql" in target:
                yield panel, target


def _run_via_dsn(dsn: str, sql: str) -> str:
    import psycopg2

    conn = psycopg2.connect(dsn)
    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.rollback()
    finally:
        conn.close()
    return ""


def _run_via_psql(container: str, user: str, dbname: str, sql: str) -> str:
    proc = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            container,
            "psql",
            "-U",
            user,
            "-v",
            "ON_ERROR_STOP=1",
            "-X",
            "-q",
            "-d",
            dbname,
        ],
        input=sql,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout or "psql falhou").strip()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default="", help="DSN Postgres (default: $DATABASE_URL)")
    parser.add_argument("--psql", action="store_true", help="executar via docker exec psql")
    parser.add_argument("--container", default="integration-incident-copilot-postgres-1")
    parser.add_argument("--user", default="iic")
    parser.add_argument("--dbname", default="iic")
    parser.add_argument(
        "--dashboards", nargs="*", default=[], help="paths dos JSON (default: todos)"
    )
    args = parser.parse_args()

    import os

    dsn = args.dsn or os.environ.get("DATABASE_URL", "")
    if not dsn and not args.psql:
        print("informe --dsn, --psql ou exporte DATABASE_URL", file=sys.stderr)
        return 2
    dsn = dsn.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+asyncpg://", "postgresql://"
    )

    paths = [pathlib.Path(p) for p in args.dashboards] or sorted(DASHBOARD_DIR.glob("*.json"))
    if not paths:
        print(f"nenhum dashboard em {DASHBOARD_DIR}", file=sys.stderr)
        return 2

    failures: list[tuple[str, str, str]] = []
    checked = 0
    for path in paths:
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        variables = _vars_of(dashboard)
        ok = 0
        for panel, target in _panels(dashboard):
            sql = normalize_sql(target["rawSql"], variables)
            title = f"{dashboard.get('uid', path.stem)} / {panel.get('title', '?')}"
            leftover = _remaining_macros(sql)
            if leftover:
                failures.append((title, f"macro nao substituida: {leftover}", ""))
                continue
            try:
                err = (
                    _run_via_psql(args.container, args.user, args.dbname, sql)
                    if args.psql
                    else _run_via_dsn(dsn, sql)
                )
            except Exception as exc:  # noqa: BLE001 - reportar e seguir
                err = f"{type(exc).__name__}: {exc}"
            checked += 1
            if err:
                failures.append((title, err.splitlines()[0][:200], ""))
            else:
                ok += 1
        print(
            f"{dashboard.get('uid', path.stem):<14} {ok} ok, {len(_list_queries(dashboard)) - ok} falha(s)"
        )

    print()
    for title, err, _ in failures:
        print(f"[FALHA] {title}\n        {err}")
    print(
        f"{checked} query(s) executada(s) | {'TODAS OK' if not failures else str(len(failures)) + ' COM ERRO'}"
    )
    return 1 if failures else 0


def _list_queries(dashboard: dict) -> list[str]:
    return [t["rawSql"] for _, t in _panels(dashboard)]


if __name__ == "__main__":
    raise SystemExit(main())
