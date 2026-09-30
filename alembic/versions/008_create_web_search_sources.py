"""DA-57: migration 008 — fontes de busca web aprovadas (web_search_sources)

Revision ID: 008
Revises: 007
Create Date: 2026-09-30

Tabela que substitui os dois mapas literais de `app/agent/nodes.py`
(`_WEB_SEARCH_SITE_MAP` e o dict `tech_term`). Ver
`app/admin/models.py::WebSearchSource` para o problema.

Decisoes que valem para quem ler o proximo diff:

- Uma linha por `interface_type` (UNIQUE), nao uma linha por site. A
  unidade de configuracao do produto e o conector; os sites de um conector
  sao uma expressao `site:a OR site:b` que pertence a ele. Multiplos sites
  continuam possiveis dentro do mesmo `site_filter`.
- SEM FK e SEM FK para `integration_systems`: a fonte de busca e
  configuracao de pesquisa, nao referencia a um sistema do catalogo (um
  conector pode ter fonte aprovada sem ter um `integration_system` com o
  mesmo `connector_type`).
- O seed das 10 linhas e' DADO, nao fallback em codigo. As duas linhas
  que faltavam nos mapas hardcoded (`successfactors` da DA-34 e `po` da
  DA-56) entram aqui com filtro proprio — antes elas perdiam tambem o
  `tech_term` e caiam no generico "SAP integration".
- A tabela e FAIL-CLOSED por leitura: interface_type sem linha = sem busca
  web. Desabilitar a linha (`enabled=False`) tem o mesmo efeito, sem
  precisar apagar o cadastro.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers
revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


#: (interface_type, site_filter, tech_term, notes) — o seed dos 10
#: conectores do Literal do pipeline. `site_filter` e' sintaxe de operador
#: do DuckDuckGo/Bing, a mesma que os mapas hardcoded usavam.
SEED = [
    (
        "odata",
        "site:help.sap.com OR site:community.sap.com/t5/technology-blogs-by-sap",
        "OData SAP Gateway",
        "DA-57: migrado do mapa hardcoded",
    ),
    (
        "rfc",
        "site:help.sap.com/docs/SAP_NETWEAVER OR site:community.sap.com OR site:github.com/SAP/PyRFC",
        "RFC ABAP BAPI",
        "DA-57: migrado do mapa hardcoded",
    ),
    (
        "servicenow",
        "site:developer.servicenow.com OR site:community.sap.com OR site:help.sap.com",
        "ServiceNow SAP integration",
        "DA-57: migrado do mapa hardcoded",
    ),
    (
        "salesforce",
        "site:developer.salesforce.com OR site:community.sap.com OR site:github.com/SAP",
        "Salesforce SAP integration",
        "DA-57: migrado do mapa hardcoded",
    ),
    (
        "workday",
        "site:community.workday.com OR site:community.sap.com",
        "Workday SAP integration",
        "DA-57: migrado do mapa hardcoded",
    ),
    (
        "ariba",
        "site:help.sap.com/docs/ARIBA OR site:community.sap.com",
        "SAP Ariba integration",
        "DA-57: migrado do mapa hardcoded",
    ),
    (
        "successfactors",
        "site:help.sap.com/docs/SUCCESSFACTORS OR site:community.sap.com",
        "SAP SuccessFactors EC integration",
        "DA-57: NOVA — successfactors (DA-34) nao existia em nenhum dos mapas hardcoded",
    ),
    (
        "cap",
        "site:cap.cloud.sap OR site:github.com/SAP/cloud-cap-samples OR site:community.sap.com",
        "SAP CAP CDS BTP",
        "DA-57: migrado do mapa hardcoded",
    ),
    (
        "apim",
        "site:help.sap.com/docs/SAP_API_MANAGEMENT OR site:community.sap.com",
        "SAP API Management",
        "DA-57: migrado do mapa hardcoded",
    ),
    (
        "po",
        "site:help.sap.com/docs/SAP_PROCESS_INTEGRATION OR site:community.sap.com",
        "SAP PI PO IDoc message monitor",
        "DA-57: NOVA — po (DA-56) nao existia em nenhum dos mapas hardcoded",
    ),
]


def upgrade() -> None:
    op.create_table(
        "web_search_sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("interface_type", sa.String(32), nullable=False, unique=True, index=True),
        sa.Column("site_filter", sa.Text(), nullable=False),
        sa.Column("tech_term", sa.String(128), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true"), index=True
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Seed: 10 linhas, uma por interface_type do Literal do pipeline.
    op.bulk_insert(
        sa.table(
            "web_search_sources",
            sa.column("interface_type", sa.String(32)),
            sa.column("site_filter", sa.Text()),
            sa.column("tech_term", sa.String(128)),
            sa.column("enabled", sa.Boolean()),
            sa.column("notes", sa.Text()),
        ),
        [
            {
                "interface_type": it,
                "site_filter": sf,
                "tech_term": tt,
                "enabled": True,
                "notes": notes,
            }
            for it, sf, tt, notes in SEED
        ],
    )


def downgrade() -> None:
    op.drop_table("web_search_sources")
