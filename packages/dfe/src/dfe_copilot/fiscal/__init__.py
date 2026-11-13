"""DA-93 (fase 2): ferramentas fiscais deterministicas do dominio DF-e.

Nenhuma funcao deste pacote chama LLM, rede ou banco. Saidas sao
`findings` do tipo "fact" (ver estudo IIC -> SAP AMS/DF-e, §6.1).
"""

from dfe_copilot.fiscal.correlation import (
    FiscalCorrelationError,
    TimelineEvent,
    diagnose_duplicity,
    diagnose_key_divergence,
    diagnose_timeout,
    normalize_events,
)
from dfe_copilot.fiscal.signature import verify_enveloped
from dfe_copilot.fiscal.xml_tools import (
    AccessKey,
    FiscalXmlError,
    SchemaPackage,
    analyze_fiscal_xml,
    parse_access_key,
    safe_parse,
    validate_xsd,
)

__all__ = [
    "AccessKey",
    "FiscalCorrelationError",
    "FiscalXmlError",
    "SchemaPackage",
    "TimelineEvent",
    "analyze_fiscal_xml",
    "diagnose_duplicity",
    "diagnose_key_divergence",
    "diagnose_timeout",
    "normalize_events",
    "parse_access_key",
    "safe_parse",
    "validate_xsd",
    "verify_enveloped",
]
