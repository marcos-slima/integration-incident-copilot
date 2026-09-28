"""DA-49 (Fase B): catalogo de sistemas integrados — integration_systems

Revision ID: 004
Revises: 003
Create Date: 2026-09-28

Catalogo operacional dos sistemas que aparecem nos incidentes
(connector_source_system / interface_type): SAP, ServiceNow, Salesforce,
Workday, Ariba, CAP, APIM — com vendor, conector, ambiente, status e
base_url gerenciados pela superficie admin, sem editar .env.

`connector_type` usa o LITERAL fechado do pipeline (odata/rfc/
servicenow/salesforce/workday/ariba/cap/apim - app/models.py) para o
sistema poder ser correlacionado ao incidente via interface_type.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers
revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_systems",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("system_key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("vendor", sa.String(64), nullable=False),
        sa.Column("connector_type", sa.String(32), nullable=False),
        sa.Column("base_url", sa.String(256), nullable=True),
        sa.Column("environment", sa.String(16), nullable=False, server_default=sa.text("'prod'")),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'active'")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("system_key", name="uq_integration_systems_key"),
    )
    op.create_index(
        "ix_integration_systems_connector_type", "integration_systems", ["connector_type"]
    )
    op.create_index("ix_integration_systems_status", "integration_systems", ["status"])
    op.create_index(
        "ix_integration_systems_vendor_key", "integration_systems", ["vendor", "system_key"]
    )


def downgrade() -> None:
    op.drop_index("ix_integration_systems_vendor_key", table_name="integration_systems")
    op.drop_index("ix_integration_systems_status", table_name="integration_systems")
    op.drop_index("ix_integration_systems_connector_type", table_name="integration_systems")
    op.drop_table("integration_systems")
