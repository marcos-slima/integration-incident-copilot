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

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.circuit_breaker import CircuitBreaker
from app.config import settings


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


# Avaliacao externa (medio prazo, item 3): "Circuit breaker nos
# conectores (ex.: tenacity + contador de falhas)". Antes desta
# mudanca, so o AI Gateway (DA-26, app/llm/gateway.py) tinha circuit
# breaker - uma falha de rede num conector HTTP (ex.: ServiceNow fora
# do ar) significava esperar o timeout completo (settings.*_timeout,
# quando existe) EM TODA chamada seguinte, mesmo sabendo que o sistema
# acabou de falhar. Singleton em nivel de modulo, compartilhado por
# TODOS os conectores (a chave e o source_system de cada um, ex.:
# "ServiceNow", "Salesforce" - circuitos independentes por sistema,
# mesma instancia de CircuitBreaker). Mesmo nao-objetivo do AI
# Gateway: in-memory, por processo, nao compartilhado entre replicas.
connector_circuit_breaker = CircuitBreaker(namespace="conn")  # DA-41: Redis distribuido


def circuit_breaker_guard(source_system: str) -> ConnectorResult | None:
    """Chamado no INICIO de `_fetch_real(...)` de cada conector, antes
    de abrir a conexao HTTP. Devolve um `ConnectorResult` de erro
    imediato (sem tentar a rede) se o circuito daquele source_system
    estiver aberto, ou `None` se a chamada pode prosseguir normalmente.

    Uso tipico dentro de um conector:

        def _fetch_real(self, identifier: str) -> ConnectorResult:
            if (blocked := circuit_breaker_guard("ServiceNow")) is not None:
                return blocked
            try:
                ...chamada HTTP real...
            except httpx.RequestError as exc:
                connector_circuit_breaker.record_failure(
                    "ServiceNow", settings.connector_circuit_failure_threshold
                )
                ...ConnectorResult de erro, como ja acontecia antes...
            else:
                connector_circuit_breaker.record_success("ServiceNow")
                ...
    """
    if connector_circuit_breaker.is_open(
        source_system, settings.connector_circuit_cooldown_seconds
    ):
        return ConnectorResult(
            source_system=source_system,
            status="error",
            error_code="CIRCUIT_OPEN",
            message=(
                f"Circuito aberto para {source_system}: muitas falhas de rede "
                "consecutivas recentes - chamada pulada sem tentar a rede de "
                "novo (aguardando o cooldown do circuit breaker)."
            ),
            raw="circuit breaker aberto (app/connectors/base.py::circuit_breaker_guard)",
            is_mock=False,
            is_fallback=True,
        )
    return None


# Avaliacao externa (nova revisao, P1 - "Injection nos conectores
# reais"): identifier (nome do iFlow, RFC destination, numero de
# IDoc/incidente/PO - ver app/models.py::IncidentRequest.identifier)
# vinha de entrada do usuario e era interpolado CRU em query strings
# (OData $filter, SOQL WHERE, ServiceNow sysparm_query, CAP $filter)
# e em segmentos de path de URL (Workday, Ariba) sem nenhum escape ou
# validacao - um identifier como "x' or 1 eq 1" (OData/SOQL) ou
# "../outro-recurso" (path) alterava a query/URL de verdade. Um
# charset restrito (SAP/ITSM/CRM ja usam so alfanumerico + separadores
# tipicos: numero de IDoc, RFC destination, CaseNumber, nome de iFlow)
# elimina a classe inteira de injection sem exigir escape especifico
# por protocolo - aspas, "/", "?", "#", espacos etc. nunca chegam a
# fazer parte da query/URL, entao nao ha o que escapar depois.
_IDENTIFIER_CHARSET_RE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")


def validate_identifier_charset(identifier: str, source_system: str) -> ConnectorResult | None:
    """Chamado no INICIO de `_fetch_real(...)` de cada conector real
    (depois do circuit_breaker_guard, antes de montar a query/URL com
    o identifier). Devolve um `ConnectorResult` de erro imediato (sem
    tentar a rede) se o identifier tiver qualquer caractere fora do
    charset permitido, ou `None` se pode prosseguir normalmente. Os
    identifiers de demo (ex.: "CPI-401-DEMO", "RFC-IDOC-51-DEMO") e
    qualquer identifier SAP/ITSM/CRM real (numero de IDoc, RFC
    destination, CaseNumber, nome de iFlow, numero de PO) ja respeitam
    esse charset - nenhum uso legitimo e afetado."""
    if not _IDENTIFIER_CHARSET_RE.match(identifier):
        return ConnectorResult(
            source_system=source_system,
            status="error",
            error_code="INVALID_IDENTIFIER",
            message=(
                f"Identificador invalido para {source_system}: aceita so letras, "
                "numeros, '.', '_' e '-' (1 a 200 caracteres). Caracteres como "
                "aspas, espacos, '/', '?' ou '#' nao sao permitidos."
            ),
            raw="",
            is_mock=False,
            is_fallback=True,
        )
    return None
