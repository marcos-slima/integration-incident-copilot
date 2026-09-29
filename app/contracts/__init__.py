"""DA-52: deteccao de drift de contrato em integracoes.

`model`   contrato normalizado + impressao digital estavel
`diff`    comparativo e classificacao de severidade (puro, sem LLM)
`odata`   leitura do `$metadata` (EDMX) de um servico OData v4
`probes`  I/O: busca o contrato do lado do SAP (httpx / pyrfc)
`observe` orquestra probe -> baseline -> diff -> sinal
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
