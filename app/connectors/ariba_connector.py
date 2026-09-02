"""Conector SAP Ariba / Business Network - representa o cenario de
referencia SAP Ariba<->S/4HANA (pedido de compra bloqueado na rede por
divergencia de dados mestre de fornecedor).

Mesmo criterio dos demais conectores reais opcionais: `ARIBA_BASE_URL`
vazio (default) = modo demo/mock; preenchido = OAuth2 Client
Credentials Grant + consulta REST sobre o status do pedido na rede.

Nao testado contra uma instancia Ariba/Business Network real (sem
acesso disponivel) - testado com `httpx.MockTransport`, mesma ressalva
dos demais conectores reais nao verificados contra sistema de
producao.

Uso:
    from app.connectors.ariba_connector import AribaConnector
    result = AribaConnector().fetch("ARIBA-PO-BLOCKED-DEMO")
"""

import httpx

from app.config import settings
from app.connectors.base import ConnectorResult, ExternalSystemConnector

_MOCK_SCENARIOS: dict[str, ConnectorResult] = {
    "ARIBA-PO-BLOCKED-DEMO": ConnectorResult(
        source_system="Ariba",
        status="error",
        error_code="SUPPLIER_MISMATCH",
        message=(
            "Pedido de compra bloqueado na Ariba Network: numero de fornecedor "
            "(ANID) nao corresponde ao cadastro de fornecedor replicado do "
            "S/4HANA (MDM de fornecedor desatualizado)"
        ),
        raw=(
            "PO_Number=ARIBA-PO-BLOCKED-DEMO\n"
            "Network_Status=Failed\n"
            "Error_Code=SUPPLIER_MISMATCH\n"
            "Supplier_ANID=AN01234567890-DEMO\n"
            "Detail=Supplier master data (LFA1) out of sync between S/4HANA and "
            "Ariba Network - last successful sync 6 days ago"
        ),
    ),
}

_DEFAULT = ConnectorResult(
    source_system="Ariba",
    status="error",
    error_code="404",
    message="Pedido nao encontrado (modo demo - identificador nao reconhecido)",
    raw="Ariba Network API: nenhum registro para o identificador informado (dados mock)",
    is_fallback=True,
)


class AribaConnector(ExternalSystemConnector):
    """`fetch(identifier)` busca por numero de pedido de compra na rede.

    `client`: injecao opcional de `httpx.Client`, mesmo padrao dos
    demais conectores reais - usado pelos testes para simular a rede
    Ariba via `httpx.MockTransport`.
    """

    def __init__(self, timeout: float = 10.0, client: httpx.Client | None = None):
        self.timeout = timeout
        self._injected_client = client

    def fetch(self, identifier: str) -> ConnectorResult:
        if not settings.ariba_base_url:
            return _MOCK_SCENARIOS.get(identifier, _DEFAULT)
        return self._fetch_real(identifier)

    def _get_access_token(self, client: httpx.Client) -> str:
        response = client.post(
            settings.ariba_oauth_token_url,
            data={"grant_type": "client_credentials"},
            auth=(settings.ariba_client_id, settings.ariba_client_secret),
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def _fetch_real(self, identifier: str) -> ConnectorResult:
        client = self._injected_client or httpx.Client(timeout=self.timeout)
        try:
            token = self._get_access_token(client)
            response = client.get(
                f"{settings.ariba_base_url.rstrip('/')}/purchase-orders/{identifier}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except httpx.HTTPStatusError as exc:
            return ConnectorResult(
                source_system="Ariba",
                status="error",
                error_code=str(exc.response.status_code),
                message=f"Falha ao obter token OAuth2 da Ariba: HTTP {exc.response.status_code}",
                raw=exc.response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )
        except httpx.RequestError as exc:
            return ConnectorResult(
                source_system="Ariba",
                status="error",
                error_code="CONNECTION_ERROR",
                message=f"Falha de rede ao consultar Ariba Network: {exc}",
                raw=str(exc),
                is_mock=False,
                is_fallback=True,
            )
        finally:
            if self._injected_client is None:
                client.close()

        if response.status_code == 404:
            return ConnectorResult(
                source_system="Ariba",
                status="error",
                error_code="404",
                message=f"Nenhum pedido Ariba encontrado para '{identifier}'",
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )
        if response.status_code != 200:
            return ConnectorResult(
                source_system="Ariba",
                status="error",
                error_code=str(response.status_code),
                message=f"Ariba Network API retornou HTTP {response.status_code}",
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )

        record = response.json()
        network_status = record.get("networkStatus", "Unknown")
        return ConnectorResult(
            source_system="Ariba",
            status="ok" if network_status in {"Confirmed", "Shipped", "Invoiced"} else "error",
            error_code=record.get("errorCode", network_status),
            message=record.get("detail", ""),
            raw=str(record),
            is_mock=False,
        )
