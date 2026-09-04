"""Interface comum para conectores de sistemas externos (SAP e nao-SAP).

Todo conector (OData, RFC, ServiceNow, e futuros como IDoc/SOAP/
Salesforce/Workday) implementa este contrato, permitindo que o grafo
LangGraph trate qualquer sistema de origem de forma uniforme -
inclusive combinando SAP com nao-SAP no mesmo incidente (ex: alerta
aberto no ServiceNow sobre uma falha de conexao RFC no SAP).

O nome da classe (`SAPConnector`) ficou historico desde quando o
projeto so falava com SAP; o contrato em si sempre foi generico
(`source_system` sempre aceitou "outros"). `ExternalSystemConnector` e
o alias recomendado para conectores novos que nao sao SAP - ver
`app/connectors/servicenow_connector.py`.

NOTA sobre "mock": todo conector segue o MESMO criterio - config
ausente = modo demo/mock (simula respostas realistas para fins de
prototipagem/portfolio, sem depender de acesso a um sistema real);
config presente = chamada real (HTTP/OAuth2 de verdade). RFC continua
mock-only ate `use_real=True` + pyrfc + SAP NetWeaver RFC SDK estarem
disponiveis (SDK exige S-user de cliente/parceiro, nao ha atalho
gratuito). Todos os demais (OData, ServiceNow, Salesforce, Workday,
Ariba, CAP) suportam o caminho real hoje - ver ARCHITECTURE.md,
secao "Conectores - mock vs. real, hoje" para o estado de validacao
de cada um.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ConnectorResult:
    source_system: str  # "OData" | "RFC" | "ServiceNow" | outros
    status: str  # "ok" | "error"
    error_code: str | None
    message: str
    raw: str  # payload/log bruto (real ou simulado, ver is_mock)
    is_mock: bool = True
    is_fallback: bool = False  # True quando o identificador nao foi reconhecido
    # (dado generico, nao um cenario real mapeado)


class SAPConnector(ABC):
    """Contrato comum para qualquer conector de sistema externo (SAP
    ou nao-SAP - ver nota de modulo acima sobre o nome historico)."""

    @abstractmethod
    def fetch(self, identifier: str) -> ConnectorResult:
        """Busca o estado/erro de uma interface a partir de um
        identificador (ex: nome do iFlow, RFC destination, numero de
        IDoc, numero de incidente ServiceNow). Implementacoes mock
        ignoram credenciais reais; implementacoes reais (ex:
        ServiceNowConnector configurado) fazem a chamada de fato.
        """
        raise NotImplementedError


# Alias preferido para conectores de sistemas que nao sao SAP (o nome
# da classe em si continua `SAPConnector` por compatibilidade com todo
# o codigo/testes existentes - ver nota de modulo).
ExternalSystemConnector = SAPConnector
