"""Adopt llm_runtime_config gateway columns that already existed in the DB.

Revision ID: 028_llm_runtime_gateway_cols
Revises: 027_align_da79_tenant_id
Create Date: 2026-10-20

These four columns were present in deployed databases but had no migration
and no ORM counterpart, so `alembic check` proposed dropping them. They hold
operator-visible tuning values (circuit breaker and backoff) and are kept:
this migration makes a fresh database match the observed schema. The Admin
UI still edits the gateway values through Settings.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "028_llm_runtime_gateway_cols"
down_revision: str | None = "027_align_da79_tenant_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COLUMNS = (
    ("llm_gateway_circuit_failure_threshold", "INTEGER", "3"),
    ("llm_gateway_circuit_cooldown_seconds", "DOUBLE PRECISION", "30.0"),
    ("llm_gateway_backoff_base_seconds", "DOUBLE PRECISION", "0.5"),
    ("llm_gateway_backoff_max_seconds", "DOUBLE PRECISION", "8.0"),
)


def upgrade() -> None:
    for name, type_, default in _COLUMNS:
        op.execute(
            f"ALTER TABLE llm_runtime_config "
            f"ADD COLUMN IF NOT EXISTS {name} {type_} DEFAULT {default}"
        )


def downgrade() -> None:
    for name, _type, _default in _COLUMNS:
        op.execute(f"ALTER TABLE llm_runtime_config DROP COLUMN IF EXISTS {name}")
