"""DB-01: system_contracts append-only garantido pelo banco (DA-52).

Revision ID: 011
Revises: 010
Create Date: 2026-10-07

A tabela e o historico das observacoes de contrato (invariante 16). O codigo
so faz INSERT, mas nada impedia um UPDATE/DELETE manual ou de outro servico
de reescrever o historico - e o antigo docs/UC_09 (removido) afirmava que havia trigger. Agora
ha: UPDATE e DELETE por linha levantam erro. TRUNCATE continua permitido
(operacao administrativa explicita, usada pelos testes e2e).
"""

from __future__ import annotations

from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION system_contracts_append_only()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'system_contracts e append-only (DA-52): % negado', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_system_contracts_append_only
        BEFORE UPDATE OR DELETE ON system_contracts
        FOR EACH ROW EXECUTE FUNCTION system_contracts_append_only();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_system_contracts_append_only ON system_contracts")
    op.execute("DROP FUNCTION IF EXISTS system_contracts_append_only()")
