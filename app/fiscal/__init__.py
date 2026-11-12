"""DA-93 (fase 2): ferramentas fiscais deterministicas do dominio DF-e.

Nenhuma funcao deste pacote chama LLM, rede ou banco. Saidas sao
`findings` do tipo "fact" (ver estudo IIC -> SAP AMS/DF-e, §6.1).
"""

from app.fiscal.xml_tools import (
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
    "FiscalXmlError",
    "SchemaPackage",
    "analyze_fiscal_xml",
    "parse_access_key",
    "safe_parse",
    "validate_xsd",
]
