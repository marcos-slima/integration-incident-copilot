#!/usr/bin/env bash
# ============================================================
# Implementa APIManagementConnector - sinal de infraestrutura de
# API (taxa de erro, violacao de politica) via Analytics do SAP
# API Management/Integration Suite.
#
# AVISO CRITICO: diferente dos 7 conectores anteriores, NAO existe
# documentacao confiavel verificada do contrato real dessa API
# (endpoint exato, formato de resposta) - buscas nao encontraram
# fonte solida especifica do SAP API Management (so referencias
# gerais de arquitetura similar, ex: MuleSoft Analytics Event API,
# que compartilha raizes conceituais mas nao e a mesma API).
#
# Implementado mesmo assim, a pedido explicito do usuario, com o
# schema marcado como ESPECULATIVO em todo lugar relevante -
# codigo, .env.example, ARCHITECTURE.md. NAO tratar como fonte de
# verdade sobre a API real ate validar contra um tenant de verdade.
#
# Uso: rodar dentro de ~/integration-incident-copilot
#   bash add_apimanagement_connector.sh
# ============================================================
set -e

if [ ! -f pyproject.toml ]; then
  echo "ERRO: rode este script dentro de ~/integration-incident-copilot"
  exit 1
fi

echo "=== 1/6 - Criando app/connectors/apimanagement_connector.py ==="
cat > app/connectors/apimanagement_connector.py << 'APIMEOF'
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
APIMEOF

echo "=== 2/6 - Registrando no factory ==="
python3 - << 'EOF'
from pathlib import Path

path = Path("app/connectors/__init__.py")
text = path.read_text(encoding="utf-8")

if "APIManagementConnector" not in text:
    text = text.replace(
        "from app.connectors.ariba_connector import AribaConnector",
        "from app.connectors.apimanagement_connector import APIManagementConnector\n"
        "from app.connectors.ariba_connector import AribaConnector",
    )
    text = text.replace(
        '    "cap": CAPConnector,',
        '    "cap": CAPConnector,\n    "apim": APIManagementConnector,',
    )
    text = text.replace(
        '    "AribaConnector",',
        '    "APIManagementConnector",\n    "AribaConnector",',
    )
    path.write_text(text, encoding="utf-8")
    print("Factory atualizado.")
else:
    print("Ja registrado, pulando.")
EOF

echo "=== 3/6 - Adicionando campos ao config.py ==="
python3 - << 'EOF'
from pathlib import Path

path = Path("app/config.py")
text = path.read_text(encoding="utf-8")

marker = '    cap_client_secret: str = ""\n    # Qdrant'
new_fields = '''    cap_client_secret: str = ""

    # SAP API Management / Integration Suite (app/connectors/
    # apimanagement_connector.py) - sinal de infraestrutura de API
    # (taxa de erro, violacao de politica). AVISO: schema de resposta
    # ESPECULATIVO, nao confirmado contra documentacao solida da API
    # real - ver docstring do modulo antes de usar contra tenant real.
    # OAuth2 Client Credentials (esse padrao em si tem confianca
    # razoavel, e generico em todo o portfolio BTP). Vazio (default) =
    # modo demo/mock, mesmo criterio dos demais conectores.
    apim_analytics_url: str = ""
    apim_oauth_token_url: str = ""
    apim_client_id: str = ""
    apim_client_secret: str = ""

    # Qdrant'''

if "apim_analytics_url" not in text:
    text = text.replace(marker, new_fields)
    path.write_text(text, encoding="utf-8")
    print("Campos adicionados.")
else:
    print("Ja existem, pulando.")
EOF

echo "=== 4/6 - Adicionando ao .env.example ==="
if ! grep -q "APIM_ANALYTICS_URL" .env.example 2>/dev/null; then
cat >> .env.example << 'ENVEOF'

# --- SAP API Management (app/connectors/apimanagement_connector.py) ---
# AVISO: schema de resposta ESPECULATIVO, nao verificado contra API
# real - ver docstring do modulo antes de configurar contra tenant real.
# Vazio = modo demo/mock. Preenchido = OAuth2 Client Credentials real.
# APIM_ANALYTICS_URL=
# APIM_OAUTH_TOKEN_URL=
# APIM_CLIENT_ID=
# APIM_CLIENT_SECRET=
ENVEOF
  echo ".env.example atualizado."
else
  echo "Ja tem, pulando."
fi

echo "=== 5/6 - Criando tests/test_apimanagement_connector.py ==="
cat > tests/test_apimanagement_connector.py << 'TESTEOF'
"""Testes do APIManagementConnector.

AVISO: valida a LOGICA do conector (parsing, guardrails, fallback),
nao o schema real da API - que e especulativo, ver docstring do
modulo app/connectors/apimanagement_connector.py.
"""

import httpx

from app.connectors.apimanagement_connector import APIManagementConnector


def test_apim_connector_mock_scenario_when_not_configured(monkeypatch):
    monkeypatch.setattr("app.connectors.apimanagement_connector.settings.apim_analytics_url", "")
    connector = APIManagementConnector()
    result = connector.fetch("APIM-RATE-LIMIT-DEMO")

    assert result.is_mock is True
    assert result.error_code == "429"


def test_apim_connector_unknown_identifier_returns_safe_fallback(monkeypatch):
    monkeypatch.setattr("app.connectors.apimanagement_connector.settings.apim_analytics_url", "")
    connector = APIManagementConnector()
    result = connector.fetch("ID-QUE-NAO-EXISTE")

    assert result.is_fallback is True
    assert result.error_code == "404"


def test_apim_connector_real_path_success(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.apimanagement_connector.settings.apim_analytics_url", "https://fake.apim/analytics"
    )
    monkeypatch.setattr(
        "app.connectors.apimanagement_connector.settings.apim_oauth_token_url", "https://fake.apim/oauth/token"
    )
    monkeypatch.setattr("app.connectors.apimanagement_connector.settings.apim_client_id", "fake-client")
    monkeypatch.setattr("app.connectors.apimanagement_connector.settings.apim_client_secret", "fake-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "fake-token"})
        return httpx.Response(
            200,
            json={
                "events": [
                    {"proxy": "proxy-pedidos-v2", "statusCode": 429, "violatedPolicyName": "RateLimit-100rpm"}
                ]
            },
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = APIManagementConnector(client=mock_client)

    result = connector.fetch("proxy-pedidos-v2")

    assert result.is_mock is False
    assert result.status == "ok"
    assert "RateLimit" in result.message
TESTEOF

echo "=== 6/6 - Sync + lint ==="
uv sync
uv run ruff check --fix app/connectors/apimanagement_connector.py app/connectors/__init__.py app/config.py tests/test_apimanagement_connector.py
uv run ruff format app/connectors/apimanagement_connector.py app/connectors/__init__.py app/config.py tests/test_apimanagement_connector.py

echo
echo "============================================================"
echo "APIManagementConnector implementado - LEMBRETE: schema"
echo "especulativo, nao validar como fonte de verdade da API real."
echo
echo "Testes:"
echo "  uv run pytest tests/test_apimanagement_connector.py -v"
echo "  uv run pytest -v"
echo "============================================================"
