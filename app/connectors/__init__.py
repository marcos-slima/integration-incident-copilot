"""Factory de conectores - SAP (OData, RFC) e nao-SAP (ServiceNow,
Salesforce, Workday, SAP Ariba)."""

from app.connectors.apimanagement_connector import APIManagementConnector
from app.connectors.ariba_connector import AribaConnector
from app.connectors.base import ConnectorResult, ExternalSystemConnector, SAPConnector
from app.connectors.cap_connector import CAPConnector
from app.connectors.odata_connector import ODataConnector
from app.connectors.rfc_connector import RFCConnector
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


def get_connector(interface_type: str) -> SAPConnector:
    cls = _REGISTRY.get(interface_type.lower())
    if cls is None:
        raise ValueError(f"Tipo de interface desconhecido: {interface_type}")
    return cls()


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
    "get_connector",
]
