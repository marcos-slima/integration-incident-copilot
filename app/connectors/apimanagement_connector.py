"""Conector SAP API Management / Integration Suite - sinal de
infraestrutura de API (taxa de erro, violacao de politica de
rate-limit/IP-blacklist) como fonte adicional de contexto para
diagnostico, complementar ao dado de negocio dos demais conectores.

########################################################################
# AVISO CRITICO - SCHEMA NAO VERIFICADO
#
# Diferente de TODOS os outros conectores deste projeto, o schema de
# resposta e o endpoint exatos usados aqui NAO foram confirmados
# contra documentacao solida do SAP API Management/Integration Suite
# real. Buscas realizadas nao encontraram fonte confiavel especifica
# da SAP para o contrato programatico da Analytics API - apenas
# referencias gerais de produtos com arquitetura conceitualmente
# similar (ex: MuleSoft Analytics Event API, que usa o conceito de
# "Violated Policy Name" em relatorios de eventos rejeitados por
# rate-limit/IP-blacklist), que INSPIRARAM o formato assumido abaixo,
# sem confirmar que e o mesmo contrato do produto SAP.
#
# NAO tratar o endpoint, os nomes de campo, nem o formato de resposta
# implementados aqui como fonte de verdade. Antes de qualquer uso
# real, validar contra um tenant SAP API Management real e corrigir
# o que estiver errado - documentado como pendencia explicita em
# ARCHITECTURE.md.
########################################################################

Autenticacao: OAuth2 Client Credentials (mesmo padrao dos demais
conectores BTP deste projeto) - essa parte tem confianca razoavel,
por ser o padrao OAuth2 generico usado consistentemente em todo o
portfolio de servicos BTP, nao especifico desta API.

Vazio (default) = modo demo/mock, mesmo criterio de todos os
conectores deste projeto.

Uso:
    from app.connectors.apimanagement_connector import APIManagementConnector
    result = APIManagementConnector().fetch("proxy-pedidos-v2")
"""

import httpx

from app.config import settings
from app.connectors.base import ConnectorResult, ExternalSystemConnector

_MOCK_SCENARIOS: dict[str, ConnectorResult] = {
    "APIM-RATE-LIMIT-DEMO": ConnectorResult(
        source_system="SAP API Management",
        status="error",
        error_code="429",
        message=(
            "Proxy de API rejeitou requisicoes por violacao de politica "
            "de rate-limit (SCHEMA ESPECULATIVO - ver aviso no docstring "
            "do modulo)"
        ),
        raw=(
            "proxy=proxy-pedidos-v2\n"
            "violatedPolicyName=RateLimit-100rpm\n"
            "statusCode=429\n"
            "eventCount=847\n"
            "[SCHEMA NAO VERIFICADO CONTRA API REAL]"
        ),
    ),
}

_DEFAULT = ConnectorResult(
    source_system="SAP API Management",
    status="error",
    error_code="404",
    message="Proxy nao encontrado (modo demo - identificador nao reconhecido)",
    raw="Analytics API: nenhum evento para o proxy informado (dados mock, schema especulativo)",
    is_fallback=True,
)


class APIManagementConnector(ExternalSystemConnector):
    """`fetch(identifier)` busca eventos de analytics para um proxy de
    API pelo nome. SCHEMA ESPECULATIVO - ver aviso no docstring do
    modulo antes de usar contra um tenant real.

    `client`: injecao opcional de `httpx.Client`, mesmo padrao dos
    demais conectores.
    """

    def __init__(self, timeout: float = 10.0, client: httpx.Client | None = None):
        self.timeout = timeout
        self._injected_client = client

    def fetch(self, identifier: str) -> ConnectorResult:
        if not settings.apim_analytics_url:
            return _MOCK_SCENARIOS.get(identifier, _DEFAULT)
        return self._fetch_real(identifier)

    def _get_access_token(self, client: httpx.Client) -> str:
        response = client.post(
            settings.apim_oauth_token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": settings.apim_client_id,
                "client_secret": settings.apim_client_secret,
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def _fetch_real(self, identifier: str) -> ConnectorResult:
        client = self._injected_client or httpx.Client(timeout=self.timeout)
        try:
            token = self._get_access_token(client)
            # ENDPOINT ESPECULATIVO - path exato nao confirmado contra
            # API real, ver aviso no docstring do modulo.
            response = client.get(
                f"{settings.apim_analytics_url.rstrip('/')}/events",
                params={"proxy": identifier},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except httpx.HTTPStatusError as exc:
            return ConnectorResult(
                source_system="SAP API Management",
                status="error",
                error_code=str(exc.response.status_code),
                message=f"Falha ao obter token OAuth2: HTTP {exc.response.status_code}",
                raw=exc.response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )
        except httpx.RequestError as exc:
            return ConnectorResult(
                source_system="SAP API Management",
                status="error",
                error_code="CONNECTION_ERROR",
                message=f"Falha de rede ao consultar API Management: {exc}",
                raw=str(exc),
                is_mock=False,
                is_fallback=True,
            )
        finally:
            if self._injected_client is None:
                client.close()

        if response.status_code != 200:
            return ConnectorResult(
                source_system="SAP API Management",
                status="error",
                error_code=str(response.status_code),
                message=f"API Management retornou HTTP {response.status_code}",
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )

        # Parsing especulativo - campos "events"/"violatedPolicyName"/
        # "statusCode" assumidos por analogia a produtos similares, NAO
        # confirmados contra o contrato real do SAP API Management.
        events = response.json().get("events", [])
        if not events:
            return ConnectorResult(
                source_system="SAP API Management",
                status="error",
                error_code="404",
                message=f"Nenhum evento encontrado para o proxy '{identifier}'",
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )

        event = events[0]
        return ConnectorResult(
            source_system="SAP API Management",
            status="ok",
            error_code=str(event.get("statusCode", "")),
            message=str(event.get("violatedPolicyName", event)),
            raw=str(event),
            is_mock=False,
        )
