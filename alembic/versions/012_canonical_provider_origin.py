"""M-11: provider_origin na forma canonica em llm_models, llm_credentials e llm_usage.

Revision ID: 012
Revises: 011
Create Date: 2026-10-07

Antes, a mesma origem aparecia em tres formatos ('api.groq.com', a URL
normalizada, o rotulo 'ollama') e o runtime/metering nunca casavam com o
registro. O codigo agora grava e consulta sempre `canonical_origin()`
(app/llm/origins.py). Esta migration converte as linhas ja gravadas.

Conflito: se a forma canonica JA existe para a mesma chave (ex.: o admin
cadastrou 'api.groq.com' e 'https://api.groq.com'), a linha antiga fica
como esta e um aviso e emitido - nao ha como decidir sozinho qual credencial
ou preco vale. Rotulos que nao sao origem ('ollama', 'local_lab') tambem
ficam como estao.

Downgrade: nao desfaz (a forma antiga era ambigua; nao ha para onde voltar).
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from alembic import op
from app.llm.origins import canonical_origin

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# (tabela, colunas que formam a chave junto com provider_origin, filtro extra)
_TABLES = (
    ("llm_credentials", (), ""),
    ("llm_models", ("model_id",), ""),
    ("llm_usage", ("model_id",), "period_end IS NULL"),
)


def upgrade() -> None:
    bind = op.get_bind()
    for table, key_cols, open_filter in _TABLES:
        rows = bind.execute(
            sa.text(f"SELECT DISTINCT provider_origin FROM {table}")  # nosec B608 - nome fixo
        ).scalars()
        for current in list(rows):
            target = canonical_origin(current)
            if not target or target == current:
                continue
            _move(bind, table, key_cols, open_filter, current, target)
        # llm_usage: periodos FECHADOS nao tem chave unica - convertem sempre.
        if table == "llm_usage":
            for current in list(
                bind.execute(sa.text("SELECT DISTINCT provider_origin FROM llm_usage")).scalars()
            ):
                target = canonical_origin(current)
                if target and target != current:
                    bind.execute(
                        sa.text(
                            "UPDATE llm_usage SET provider_origin = :t "
                            "WHERE provider_origin = :c AND period_end IS NOT NULL"
                        ),
                        {"t": target, "c": current},
                    )


def _move(bind, table: str, key_cols: tuple[str, ...], open_filter: str, current: str, target: str):
    cols = ", ".join(key_cols) if key_cols else "1"
    where_open = f" AND {open_filter}" if open_filter else ""
    for key in bind.execute(
        sa.text(
            f"SELECT {cols} FROM {table} WHERE provider_origin = :c{where_open}"  # nosec B608
        ),
        {"c": current},
    ).all():
        params = {"c": current, "t": target}
        match = ""
        for i, col in enumerate(key_cols):
            params[f"k{i}"] = key[i]
            match += f" AND {col} = :k{i}"
        exists = bind.execute(
            sa.text(
                f"SELECT 1 FROM {table} WHERE provider_origin = :t{match}{where_open}"  # nosec B608
            ),
            params,
        ).first()
        if exists:
            logger.warning(
                "M-11: %s ja tem a origem canonica '%s' para a mesma chave; "
                "linha com '%s' mantida - resolva manualmente no /admin.",
                table,
                target,
                current,
            )
            continue
        bind.execute(
            sa.text(
                f"UPDATE {table} SET provider_origin = :t "  # nosec B608
                f"WHERE provider_origin = :c{match}{where_open}"
            ),
            params,
        )


def downgrade() -> None:
    # Normalizacao irreversivel por definicao (ver docstring).
    pass
