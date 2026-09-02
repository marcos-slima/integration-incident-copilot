"""Factory de conectores - SAP (OData, RFC) e nao-SAP (ServiceNow)."""

from app.connectors.base import ConnectorResult, ExternalSystemConnector, SAPConnector
from app.connectors.odata_connector import ODataConnector
from app.connectors.rfc_connector import RFCConnector
from app.connectors.servicenow_connector import ServiceNowConnector

_REGISTRY: dict[str, type[SAPConnector]] = {
    "odata": ODataConnector,
    "rfc": RFCConnector,
    "servicenow": ServiceNowConnector,
}


def get_connector(interface_type: str) -> SAPConnector:
    cls = _REGISTRY.get(interface_type.lower())
    if cls is None:
        raise ValueError(f"Tipo de interface desconhecido: {interface_type}")
    return cls()


__all__ = [
    "ConnectorResult",
    "ExternalSystemConnector",
    "ODataConnector",
    "RFCConnector",
    "SAPConnector",
    "ServiceNowConnector",
    "get_connector",
]
