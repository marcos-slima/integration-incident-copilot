#!/usr/bin/env bash
# ============================================================
# Implementa CAPConnector - servicos SAP CAP via OData v4,
# autenticacao XSUAA (OAuth2 Client Credentials, Basic Auth no
# token endpoint - padrao XSUAA, diferente do body-params usado
# pelos demais conectores).
#
# Segue exatamente o padrao ja estabelecido:
#   config ausente = mock; config presente = chamada real;
#   testado via httpx.MockTransport (sem BTP real no CI).
#
# Uso: rodar dentro de ~/integration-incident-copilot
#   bash add_cap_connector.sh
# ============================================================
set -e

if [ ! -f pyproject.toml ]; then
  echo "ERRO: rode este script dentro de ~/integration-incident-copilot"
  exit 1
fi

echo "=== 1/6 - Criando app/connectors/cap_connector.py ==="
cat > app/connectors/cap_connector.py << 'CAPEOF'
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
CAPEOF

echo "=== 2/6 - Registrando no factory (app/connectors/__init__.py) ==="
python3 - << 'PYEOF'
from pathlib import Path

path = Path("app/connectors/__init__.py")
text = path.read_text(encoding="utf-8")

if "CAPConnector" not in text:
    text = text.replace(
        "from app.connectors.base import ConnectorResult, ExternalSystemConnector, SAPConnector",
        "from app.connectors.base import ConnectorResult, ExternalSystemConnector, SAPConnector\n"
        "from app.connectors.cap_connector import CAPConnector",
    )
    text = text.replace(
        '    "ariba": AribaConnector,',
        '    "ariba": AribaConnector,\n    "cap": CAPConnector,',
    )
    text = text.replace(
        '    "AribaConnector",\n    "ConnectorResult",',
        '    "AribaConnector",\n    "CAPConnector",\n    "ConnectorResult",',
    )
    path.write_text(text, encoding="utf-8")
    print("Factory atualizado com CAPConnector.")
else:
    print("CAPConnector ja registrado, pulando.")
PYEOF

echo "=== 3/6 - Adicionando campos ao app/config.py ==="
python3 - << 'PYEOF'
from pathlib import Path

path = Path("app/config.py")
text = path.read_text(encoding="utf-8")

marker = '    # Qdrant\n    qdrant_url: str = "http://127.0.0.1:6333"'
new_fields = '''    # SAP CAP (app/connectors/cap_connector.py) - OData v4 (protocolo
    # default de qualquer servico CAP, caminho recomendado pelo Clean
    # Core) + XSUAA (OAuth2 Client Credentials, Basic Auth no token
    # endpoint - client vinculado a um subaccount/service instance do
    # BTP, diferente de um client OAuth2 "solto"). Vazio (default) =
    # modo demo/mock, mesmo criterio dos demais conectores.
    cap_service_url: str = ""
    cap_xsuaa_token_url: str = ""
    cap_client_id: str = ""
    cap_client_secret: str = ""

    # Qdrant
    qdrant_url: str = "http://127.0.0.1:6333"'''

if "cap_service_url" not in text:
    text = text.replace(marker, new_fields)
    path.write_text(text, encoding="utf-8")
    print("Campos CAP adicionados ao Settings.")
else:
    print("Campos CAP ja existem, pulando.")
PYEOF

echo "=== 4/6 - Adicionando ao .env.example ==="
if ! grep -q "CAP_SERVICE_URL" .env.example 2>/dev/null; then
cat >> .env.example << 'ENVEOF'

# --- SAP CAP (app/connectors/cap_connector.py) ---
# Vazio = modo demo/mock. Preenchido = OData v4 real + XSUAA (OAuth2
# Client Credentials, Basic Auth no token endpoint).
# CAP_SERVICE_URL=https://seu-app.cfapps.us10.hana.ondemand.com/odata/v4/po-approval/PurchaseOrderApprovals
# CAP_XSUAA_TOKEN_URL=https://seu-subaccount.authentication.us10.hana.ondemand.com/oauth/token
# CAP_CLIENT_ID=
# CAP_CLIENT_SECRET=
ENVEOF
  echo ".env.example atualizado."
else
  echo ".env.example ja tem CAP, pulando."
fi

echo "=== 5/6 - Criando documento de conhecimento de referencia ==="
cat > data/sample_docs/cap_custom_purchase_approval_failure.md << 'DOCEOF'
# Servico CAP Customizado - Falha ao Processar Aprovacao de Pedido de Compra

## Sintoma
Um servico CAP customizado (extensao de aprovacao de pedido de
compra, side-by-side no BTP) rejeita requisicoes de um consumidor
externo com HTTP 422, ao inves de processar a aprovacao.

## Causas comuns
- Payload do consumidor externo nao inclui um campo obrigatorio do
  modelo CDS (ex: `approverId`), geralmente por desalinhamento entre
  o contrato OData v4 exposto pelo CAP e o que o consumidor espera
- Anotacao `@mandatory`/`@assert.range` no CDS mais restritiva do que
  o consumidor externo foi construido para respeitar
- Versao do modelo de dados evoluiu (novo campo obrigatorio) sem o
  consumidor externo ser atualizado junto

## Diagnostico
1. Verificar o corpo do erro OData v4 retornado (`error.code`,
   `error.message`) - CAP costuma expor a anotacao CDS que falhou
2. Comparar o payload enviado pelo consumidor com o metadata OData v4
   atual do servico (`$metadata`)
3. Checar se houve deploy recente do servico CAP que adicionou
   validacao nova

## Resolucao tipica
Alinhar o payload do consumidor externo ao contrato atual do servico
(campo obrigatorio ausente), ou, se a mudanca de contrato foi
intencional, versionar o endpoint OData v4 para nao quebrar
consumidores existentes.
DOCEOF

echo "=== 6/6 - Criando tests/test_cap_connector.py ==="
cat > tests/test_cap_connector.py << 'TESTEOF'
"""Testes do CAPConnector - mesmo padrao dos demais conectores reais
(httpx.MockTransport simulando token XSUAA + consulta OData v4)."""

import httpx

from app.connectors.cap_connector import CAPConnector


def test_cap_connector_mock_scenario_when_not_configured():
    connector = CAPConnector()
    result = connector.fetch("CAP-PO-APPROVAL-DEMO")

    assert result.is_mock is True
    assert result.error_code == "422"
    assert "aprovacao" in result.message.lower() or "approver" in result.raw.lower()


def test_cap_connector_unknown_identifier_returns_safe_fallback():
    connector = CAPConnector()
    result = connector.fetch("ID-QUE-NAO-EXISTE")

    assert result.is_fallback is True
    assert result.error_code == "404"


def test_cap_connector_real_path_success(monkeypatch):
    monkeypatch.setattr("app.connectors.cap_connector.settings.cap_service_url", "https://fake.cap/odata/v4/svc")
    monkeypatch.setattr(
        "app.connectors.cap_connector.settings.cap_xsuaa_token_url", "https://fake.xsuaa/oauth/token"
    )
    monkeypatch.setattr("app.connectors.cap_connector.settings.cap_client_id", "fake-client")
    monkeypatch.setattr("app.connectors.cap_connector.settings.cap_client_secret", "fake-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "fake-token", "expires_in": 3600})
        return httpx.Response(
            200,
            json={
                "value": [
                    {"ID": "PO-APPROVAL-00042", "status": "rejected", "message": "campo obrigatorio ausente"}
                ]
            },
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = CAPConnector(client=mock_client)

    result = connector.fetch("PO-APPROVAL-00042")

    assert result.is_mock is False
    assert result.status == "ok"
    assert "campo obrigatorio" in result.message


def test_cap_connector_real_path_token_failure(monkeypatch):
    monkeypatch.setattr("app.connectors.cap_connector.settings.cap_service_url", "https://fake.cap/odata/v4/svc")
    monkeypatch.setattr(
        "app.connectors.cap_connector.settings.cap_xsuaa_token_url", "https://fake.xsuaa/oauth/token"
    )
    monkeypatch.setattr("app.connectors.cap_connector.settings.cap_client_id", "fake-client")
    monkeypatch.setattr("app.connectors.cap_connector.settings.cap_client_secret", "wrong-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client"})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = CAPConnector(client=mock_client)

    result = connector.fetch("PO-APPROVAL-00042")

    assert result.is_mock is False
    assert result.is_fallback is True
    assert result.error_code == "401"
TESTEOF

echo "=== Sync + lint ==="
uv sync
uv run ruff check --fix app/connectors/cap_connector.py app/connectors/__init__.py app/config.py tests/test_cap_connector.py
uv run ruff format app/connectors/cap_connector.py app/connectors/__init__.py app/config.py tests/test_cap_connector.py

echo
echo "============================================================"
echo "CAPConnector implementado. Testes (sem infraestrutura externa):"
echo
echo "  uv run pytest tests/test_cap_connector.py -v"
echo
echo "Suite completa (garante zero regressao):"
echo "  uv run pytest -v"
echo
echo "APIManagementConnector fica documentado como proximo passo,"
echo "NAO implementado agora - mesma disciplina de um de cada vez."
echo "============================================================"
