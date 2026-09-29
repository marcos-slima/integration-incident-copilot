"""DA-52: deteccao de drift de contrato em integracoes.

`model`     contrato normalizado + impressao digital estavel
`odata`     leitura do `$metadata` (EDMX) de um servico OData v4
`diff`      comparativo e classificacao de severidade (puro, sem LLM)
`baseline`  persistencia da observacao (ORM `system_contracts`, migration 005)
`observe`   orquestra probe -> diff -> baseline -> sinal

Nao ha modulo `probes` aqui de proposito: a leitura do contrato e'
responsabilidade do CONECTOR, via `SAPConnector.fetch_contract()`
(app/connectors/base.py). Quem tem a credencial e quem sabe falar com o SAP
e' o conector — o detector nao deveria carregar segredo de sistema nenhum
para fazer uma leitura. Os 7 conectores sem introspeccao herdam `None` e
caem em `unverified`.
"""

from app.contracts.diff import (
    SEVERITY_ADDITIVE,
    SEVERITY_BREAKING,
    SEVERITY_COSMETIC,
    SEVERITY_NONE,
    Change,
    DriftReport,
    ObservationStatus,
    diff_contracts,
    unverified_report,
)
from app.contracts.model import (
    KIND_IDOC,
    KIND_ODATA,
    KIND_RFC_FM,
    Contract,
    Entity,
    Property,
)
from app.contracts.odata import MetadataError, contract_from_dict, parse_odata_metadata

__all__ = [
    "KIND_IDOC",
    "KIND_ODATA",
    "KIND_RFC_FM",
    "SEVERITY_ADDITIVE",
    "SEVERITY_BREAKING",
    "SEVERITY_COSMETIC",
    "SEVERITY_NONE",
    "Change",
    "Contract",
    "DriftReport",
    "Entity",
    "MetadataError",
    "ObservationStatus",
    "Property",
    "contract_from_dict",
    "diff_contracts",
    "parse_odata_metadata",
    "unverified_report",
]
