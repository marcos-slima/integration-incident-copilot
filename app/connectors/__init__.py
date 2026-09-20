"""Factory de conectores - SAP (OData, RFC) e nao-SAP (ServiceNow,
Salesforce, Workday, SAP Ariba)."""

from app.config import settings
from app.connectors.apimanagement_connector import APIManagementConnector
from app.connectors.ariba_connector import AribaConnector
from app.connectors.base import ConnectorResult, ExternalSystemConnector, SAPConnector
from app.connectors.cap_connector import CAPConnector
from app.connectors.odata_connector import ODataConnector
from app.connectors.rfc_connector import HAS_PYRFC, RFCConnector
from app.connectors.salesforce_connector import SalesforceConnector
from app.connectors.servicenow_connector import ServiceNowConnector
from app.connectors.workday_connector import WorkdayConnector

_REGISTRY: dict[str, type[SAPConnector]] = {
    "odata": ODataConnector,
    "rfc": RFCConnector,
    "servicenow": ServiceNowConnector,
    "salesforce": SalesforceConnector,
    "workday": WorkdayConnector,
    "ariba": AribaConnector,
    "cap": CAPConnector,
    "apim": APIManagementConnector,
}

# Setting que cada conector usa para decidir modo real vs mock dentro do
# proprio fetch() (ver cada arquivo em app/connectors/) - fonte unica para
# GET /health nao duplicar (e arriscar divergir de) essa logica.
_REAL_MODE_SETTING: dict[str, str] = {
    "odata": "odata_service_url",
    "rfc": "sap_ashost",
    "servicenow": "servicenow_instance_url",
    "salesforce": "salesforce_instance_url",
    "workday": "workday_tenant",
    "ariba": "ariba_base_url",
    "cap": "cap_service_url",
    "apim": "apim_analytics_url",
}


def get_connector(interface_type: str) -> SAPConnector:
    cls = _REGISTRY.get(interface_type.lower())
    if cls is None:
        raise ValueError(f"Tipo de interface desconhecido: {interface_type}")
    return cls()


def connector_status() -> dict[str, dict[str, str]]:
    """Estado de cada conector (registry inteiro, mesma ordem de
    _REGISTRY) DERIVADO da mesma configuracao que cada fetch() usa -
    usado por GET /health para o frontend (StatusView) parar de
    mostrar uma lista hardcoded que nao reflete o .env atual.

    status: "real" (setting configurado, vai tentar chamar o sistema
    de verdade), "mock" (setting vazio, usa cenarios de demonstracao) ou
    "misconfigured" (setting configurado, mas falta alguma dependencia
    para o modo real funcionar - hoje so o RFC/pyrfc)."""
    result: dict[str, dict[str, str]] = {}
    for name, setting_attr in _REAL_MODE_SETTING.items():
        configured = bool(getattr(settings, setting_attr))
        if name == "rfc" and configured and not HAS_PYRFC:
            status = "misconfigured"
            note = "SAP_ASHOST configurado, mas pacote 'pyrfc'/SDK NetWeaver RFC ausente"
        elif configured:
            status = "real"
            note = f"{setting_attr.upper()} configurado"
        else:
            status = "mock"
            note = f"{setting_attr.upper()} nao configurado"
        result[name] = {"status": status, "note": note}
    return result


__all__ = [
    "APIManagementConnector",
    "AribaConnector",
    "CAPConnector",
    "ConnectorResult",
    "ExternalSystemConnector",
    "ODataConnector",
    "RFCConnector",
    "SAPConnector",
    "SalesforceConnector",
    "ServiceNowConnector",
    "WorkdayConnector",
    "connector_status",
    "get_connector",
]
