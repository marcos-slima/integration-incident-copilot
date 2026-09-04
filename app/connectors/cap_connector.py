"""Conector SAP CAP (Cloud Application Programming Model) - representa
o cenario de referencia de um servico CAP customizado (ex: extensao de
aprovacao de pedido de compra) falhando ao processar uma requisicao de
um consumidor externo.

Protocolo: OData v4 - formato default de qualquer servico CAP, e o
caminho recomendado pelo Clean Core para integracao com terceiros (em
vez de REST/GraphQL, que o CAP tambem suporta mas sao secundarios/
opcionais - so implementar se um servico real especifico exigir, nao
de forma especulativa).

Autenticacao: XSUAA (Authorization and Trust Management service do
BTP), OAuth2 Client Credentials Flow. Diferenca importante em relacao
aos demais conectores deste projeto: o client XSUAA e vinculado a um
subaccount/service instance especifico do BTP (nao um OAuth2 client
"solto" como em Salesforce/Workday/Ariba) - o token endpoint em si ja
identifica de qual subaccount o client pertence. XSUAA tambem espera
Basic Auth no request de token (client_id:client_secret no header
Authorization), nao client_id/client_secret no corpo da requisicao
como os demais conectores fazem.

Vazio (default) = modo demo/mock, mesmo criterio de todos os
conectores deste projeto. Nao testado contra um servico CAP real em
producao continua - testado uma vez contra uma instancia BTP Trial
(90 dias, sem cartao, descartavel por natureza) e documentado o
resultado, sem depender de manter essa infraestrutura no ar
indefinidamente. Ver ARCHITECTURE.md para o resultado dessa validacao.

Uso:
    from app.connectors.cap_connector import CAPConnector
    result = CAPConnector().fetch("PO-APPROVAL-00042")
"""

import httpx

from app.config import settings
from app.connectors.base import ConnectorResult, ExternalSystemConnector

_MOCK_SCENARIOS: dict[str, ConnectorResult] = {
    "CAP-PO-APPROVAL-DEMO": ConnectorResult(
        source_system="SAP CAP",
        status="error",
        error_code="422",
        message=(
            "Servico CAP de aprovacao de pedido de compra rejeitou a "
            "requisicao de um consumidor externo - payload nao passa na "
            "validacao de entidade (campo obrigatorio ausente no evento "
            "recebido)"
        ),
        raw=(
            "POST /odata/v4/po-approval/PurchaseOrderApprovals\n"
            "HTTP/1.1 422 Unprocessable Entity\n"
            '{"error": {"code": "ASSERT_ERROR", "message": '
            "\"Value required for field 'approverId'\"}}"
        ),
    ),
}

_DEFAULT = ConnectorResult(
    source_system="SAP CAP",
    status="error",
    error_code="404",
    message="Registro CAP nao encontrado (modo demo - identificador nao reconhecido)",
    raw="OData v4: nenhuma entidade para o identificador informado (dados mock)",
    is_fallback=True,
)


class CAPConnector(ExternalSystemConnector):
    """`fetch(identifier)` busca uma entidade no servico CAP por ID via
    OData v4 ($filter).

    `client`: injecao opcional de `httpx.Client`, mesmo padrao dos
    demais conectores - usado pelos testes para simular o servico CAP
    via `httpx.MockTransport` sem depender de uma instancia BTP real.
    """

    def __init__(self, timeout: float = 10.0, client: httpx.Client | None = None):
        self.timeout = timeout
        self._injected_client = client

    def fetch(self, identifier: str) -> ConnectorResult:
        if not settings.cap_service_url:
            return _MOCK_SCENARIOS.get(identifier, _DEFAULT)
        return self._fetch_real(identifier)

    def _get_access_token(self, client: httpx.Client) -> str:
        response = client.post(
            settings.cap_xsuaa_token_url,
            auth=httpx.BasicAuth(settings.cap_client_id, settings.cap_client_secret),
            data={"grant_type": "client_credentials"},
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def _fetch_real(self, identifier: str) -> ConnectorResult:
        client = self._injected_client or httpx.Client(timeout=self.timeout)
        try:
            token = self._get_access_token(client)
            response = client.get(
                settings.cap_service_url.rstrip("/"),
                params={"$filter": f"ID eq '{identifier}'"},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except httpx.HTTPStatusError as exc:
            return ConnectorResult(
                source_system="SAP CAP",
                status="error",
                error_code=str(exc.response.status_code),
                message=f"Falha ao obter token XSUAA: HTTP {exc.response.status_code}",
                raw=exc.response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )
        except httpx.RequestError as exc:
            return ConnectorResult(
                source_system="SAP CAP",
                status="error",
                error_code="CONNECTION_ERROR",
                message=f"Falha de rede ao consultar o servico CAP: {exc}",
                raw=str(exc),
                is_mock=False,
                is_fallback=True,
            )
        finally:
            if self._injected_client is None:
                client.close()

        if response.status_code != 200:
            return ConnectorResult(
                source_system="SAP CAP",
                status="error",
                error_code=str(response.status_code),
                message=f"Servico CAP (OData v4) retornou HTTP {response.status_code}",
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )

        records = response.json().get("value", [])  # OData v4 usa "value", nao "records"
        if not records:
            return ConnectorResult(
                source_system="SAP CAP",
                status="error",
                error_code="404",
                message=f"Nenhuma entidade CAP encontrada para '{identifier}'",
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )

        record = records[0]
        return ConnectorResult(
            source_system="SAP CAP",
            status="ok",
            error_code=str(record.get("status", "")),
            message=str(record.get("message", record)),
            raw=str(record),
            is_mock=False,
        )
