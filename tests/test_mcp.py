"""Testes do servidor MCP (app/mcp/server.py) - DA-19.

Duas camadas testadas separadamente:
1. Logica das tools (`diagnose_incident`, `list_connectors`) chamadas
   diretamente como funcoes Python puras - o decorator `@mcp.tool()`
   nao envolve a funcao original (ver mcp.server.mcpserver.server.tool,
   `decorator` retorna `fn` inalterado), entao testar assim cobre a
   MESMA logica que roda via protocolo MCP de verdade, sem a
   complexidade/fragilidade de simular um handshake completo do
   transporte streamable-http em teste.
2. O middleware de autenticacao (`RequireApiKeyMiddleware`), via
   TestClient no app FastAPI real com o sub-app MCP montado em /mcp -
   essa e a superficie nova de risco real (DA-19 reusa settings.api_key
   do DA-18), entao e testada na integracao HTTP de verdade.
"""

from fastapi.testclient import TestClient

import app.mcp.server as mcp_server_module
from app.config import Settings
from app.main import app
from app.mcp.server import diagnose_incident, list_connectors
from app.models import DiagnosisResponse

client = TestClient(app)


def test_list_connectors_returns_all_registered_interface_types():
    catalog = list_connectors()
    interface_types = {row["interface_type"] for row in catalog}
    assert interface_types == {
        "odata",
        "rfc",
        "servicenow",
        "salesforce",
        "workday",
        "ariba",
        "cap",
        "apim",
    }
    for row in catalog:
        assert isinstance(row["real_data_configured"], bool)
        assert row["config_hint"]
        assert row["vendor"]


def test_list_connectors_rfc_is_always_mock_no_env_flag():
    catalog = {row["interface_type"]: row for row in list_connectors()}
    assert catalog["rfc"]["real_data_configured"] is False
    assert "use_real=True" in catalog["rfc"]["config_hint"]


def test_list_connectors_reflects_configured_real_data(monkeypatch):
    monkeypatch.setattr(
        mcp_server_module.settings, "servicenow_instance_url", "https://dev12345.service-now.com"
    )
    catalog = {row["interface_type"]: row for row in list_connectors()}
    assert catalog["servicenow"]["real_data_configured"] is True
    assert catalog["odata"]["real_data_configured"] is False  # nao afetado


def test_diagnose_incident_tool_calls_run_diagnosis_and_returns_dict(monkeypatch):
    def _stub_run_diagnosis(request):
        assert request.description == "iFlow falhando com erro 401"
        return DiagnosisResponse(
            probable_root_cause="Causa raiz de teste (stub)",
            model_confidence=0.75,
            diagnosis_confidence=0.0,
            next_steps=["Passo 1"],
            report_markdown="## Diagnostico\n\nCausa raiz de teste (stub)",
            matched_source="doc_teste.md",
        )

    monkeypatch.setattr(mcp_server_module, "run_diagnosis", _stub_run_diagnosis)

    result = diagnose_incident(description="iFlow falhando com erro 401")

    assert result["probable_root_cause"] == "Causa raiz de teste (stub)"
    assert result["model_confidence"] == 0.75
    assert result["matched_source"] == "doc_teste.md"


def test_mcp_endpoint_requires_api_key(monkeypatch):
    """DA-19/DA-18: /mcp reusa o mesmo X-API-Key de /diagnose - sem
    header, com header errado, e so entao passa a checagem (a
    requisicao pode falhar depois por nao seguir o protocolo MCP
    streamable-http completo neste teste, mas NAO deve mais ser
    401 - o que prova que o middleware deixou passar).

    Usa `with TestClient(app) as scoped_client` (em vez do `client`
    módulo-level usado no resto do arquivo) porque o app MCP montado
    em /mcp só fica utilizável depois que o lifespan do app raiz roda
    `mcp_server.session_manager.run()` (DA-19) - sem isso o SDK MCP
    responde com RuntimeError("Task group is not initialized"), não
    relacionado à autenticação que este teste verifica."""
    monkeypatch.setattr(mcp_server_module, "settings", Settings(api_key="secret-mcp"))

    # DA-19: StreamableHTTPSessionManager.run() so pode rodar UMA vez
    # por instancia de processo (o SDK MCP levanta RuntimeError na
    # segunda tentativa) - `app` e um singleto global (mesmo modulo
    # `app.main` de todos os testes), entao so pode existir UM
    # `with TestClient(app) as ...` (um ciclo de lifespan) em todo o
    # arquivo de teste. Por isso as duas verificacoes (com barra final
    # e sem) ficam no MESMO bloco, em vez de dois `with` separados.
    with TestClient(app, follow_redirects=False) as scoped_client:
        # Sem barra final: 307 redirecionando para a forma correta,
        # NUNCA um 200/passthrough silencioso sem checar a chave -
        # comportamento padrao de `app.mount()` no Starlette, nao
        # especifico do MCP (ver DEPLOY.md).
        bare_path = scoped_client.post("/mcp", json={})
        assert bare_path.status_code == 307
        assert bare_path.headers["location"].endswith("/mcp/")

        # Com barra final (path real usado por um cliente MCP - o SDK
        # oficial ja monta a URL assim): a checagem de X-API-Key roda de
        # verdade.
        unauthorized = scoped_client.post("/mcp/", json={})
        assert unauthorized.status_code == 401

        wrong_key = scoped_client.post("/mcp/", json={}, headers={"X-API-Key": "chave-errada"})
        assert wrong_key.status_code == 401

        authorized = scoped_client.post(
            "/mcp/",
            json={},
            headers={
                "X-API-Key": "secret-mcp",
                "Accept": "application/json, text/event-stream",
            },
        )
        assert authorized.status_code != 401
