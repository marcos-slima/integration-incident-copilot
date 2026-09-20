"""Testes da camada A2A (app/a2a/) - Agent Card, JSON-RPC (message/send,
tasks/get), autenticacao opcional. Usa um `diagnosis_fn` STUB injetado
no TaskManager (mesmo padrao de injecao de dependencia dos conectores
HTTP) para nao depender de um LLM real no ar - a orquestracao real
(`run_diagnosis`) ja e coberta pelos testes de integracao existentes
de `/diagnose` (`tests/test_api.py`), e a camada A2A chama exatamente
a mesma funcao por design (ver app/a2a/task_manager.py) - nao ha logica
de diagnostico duplicada para testar de novo aqui.
"""

import pytest
from fastapi.testclient import TestClient

import app.a2a.server as a2a_server
from app.a2a.task_manager import TaskManager
from app.config import Settings
from app.main import app
from app.models import DiagnosisResponse

client = TestClient(app)


def _stub_diagnosis(request) -> DiagnosisResponse:
    return DiagnosisResponse(
        probable_root_cause="Causa raiz de teste (stub)",
        confidence=0.75,
        next_steps=["Passo 1", "Passo 2"],
        report_markdown="## Diagnostico\n\nCausa raiz de teste (stub)",
        matched_source="doc_teste.md",
    )


@pytest.fixture(autouse=True)
def stub_task_manager(monkeypatch):
    manager = TaskManager(diagnosis_fn=_stub_diagnosis)
    monkeypatch.setattr(a2a_server, "get_default_task_manager", lambda: manager)
    return manager


def test_agent_card_is_published_at_well_known_path():
    response = client.get("/.well-known/agent-card.json")
    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "SAP Integration Copilot"
    assert card["skills"][0]["id"] == "diagnose-integration-incident"
    # sem A2A_API_KEY configurado (default de teste, TestClient nao roda o
    # lifespan que aciona _ensure_api_keys_configured - DA-18) - sem
    # securitySchemes. Ver test_agent_card_reflects_configured_a2a_api_key
    # para o card com uma chave configurada.
    assert card["security"] == []


def test_agent_card_reflects_configured_a2a_api_key(monkeypatch):
    """DA-18: quando A2A_API_KEY esta configurada (seja via .env, seja
    porque _ensure_api_keys_configured gerou uma no startup real), o
    Agent Card deve anunciar o securityScheme - agentes que consomem o
    card programaticamente dependem disso pra saber que devem enviar
    o header X-A2A-Api-Key."""
    monkeypatch.setattr("app.a2a.agent_card.settings", Settings(a2a_api_key="secret-card"))
    from app.a2a.agent_card import get_agent_card

    card = get_agent_card()
    assert card["security"] == [{"apiKeyAuth": []}]
    assert card["securitySchemes"]["apiKeyAuth"]["name"] == "X-A2A-Api-Key"


def test_message_send_runs_diagnosis_and_returns_completed_task():
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "iFlow falhando com erro 401"}],
            }
        },
    }
    response = client.post("/a2a", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    result = body["result"]
    assert result["status"]["state"] == "completed"
    assert (
        result["artifacts"][0]["parts"][0]["text"] == "## Diagnostico\n\nCausa raiz de teste (stub)"
    )
    assert result["metadata"]["probable_root_cause"] == "Causa raiz de teste (stub)"


def test_message_send_with_structured_data_part():
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [
                    {"kind": "text", "text": "RFC destination indisponivel"},
                    {"kind": "data", "data": {"interface_type": "rfc", "identifier": "RFC-X"}},
                ],
            }
        },
    }
    response = client.post("/a2a", json=payload)
    assert response.status_code == 200
    assert response.json()["result"]["status"]["state"] == "completed"


def test_message_send_without_message_param_is_invalid_params():
    response = client.post(
        "/a2a", json={"jsonrpc": "2.0", "id": 3, "method": "message/send", "params": {}}
    )
    body = response.json()
    assert body["error"]["code"] == -32602


def test_tasks_get_roundtrip():
    send_response = client.post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "message/send",
            "params": {
                "message": {"role": "user", "parts": [{"kind": "text", "text": "incidente x"}]}
            },
        },
    )
    task_id = send_response.json()["result"]["id"]

    get_response = client.post(
        "/a2a", json={"jsonrpc": "2.0", "id": 5, "method": "tasks/get", "params": {"id": task_id}}
    )
    body = get_response.json()
    assert body["result"]["id"] == task_id
    assert body["result"]["status"]["state"] == "completed"


def test_tasks_get_unknown_id_returns_task_not_found_error():
    response = client.post(
        "/a2a",
        json={"jsonrpc": "2.0", "id": 6, "method": "tasks/get", "params": {"id": "nao-existe"}},
    )
    body = response.json()
    assert body["error"]["code"] == -32001


def test_unknown_method_returns_method_not_found_error():
    response = client.post("/a2a", json={"jsonrpc": "2.0", "id": 7, "method": "tasks/cancel"})
    body = response.json()
    assert body["error"]["code"] == -32601


def test_failed_diagnosis_becomes_failed_task_not_http_error(monkeypatch):
    def _raising_diagnosis(request):
        raise RuntimeError("LLM indisponivel (simulado)")

    manager = TaskManager(diagnosis_fn=_raising_diagnosis)
    monkeypatch.setattr(a2a_server, "get_default_task_manager", lambda: manager)

    response = client.post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": 8,
            "method": "message/send",
            "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "x"}]}},
        },
    )

    body = response.json()
    assert response.status_code == 200  # erro de negocio, nao de transporte
    assert body["result"]["status"]["state"] == "failed"
    assert "LLM indisponivel" in body["result"]["error"]


def test_a2a_endpoint_requires_api_key_when_configured(monkeypatch):
    monkeypatch.setattr("app.a2a.server.settings", Settings(a2a_api_key="secret-123"))
    payload = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "message/send",
        "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "x"}]}},
    }

    unauthorized = client.post("/a2a", json=payload)
    assert unauthorized.status_code == 401

    authorized = client.post("/a2a", json=payload, headers={"X-A2A-Api-Key": "secret-123"})
    assert authorized.status_code == 200
    assert authorized.json()["result"]["status"]["state"] == "completed"


def test_a2a_endpoint_rejects_wrong_api_key_when_configured(monkeypatch):
    """DA-18: uma chave incorreta deve ser rejeitada com o mesmo 401 -
    nao basta so testar "sem header"; um caller que manda uma chave
    errada (typo, chave revogada) tem que cair no mesmo caminho de erro."""
    monkeypatch.setattr("app.a2a.server.settings", Settings(a2a_api_key="secret-123"))
    payload = {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "message/send",
        "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "x"}]}},
    }

    response = client.post("/a2a", json=payload, headers={"X-A2A-Api-Key": "chave-errada"})
    assert response.status_code == 401


def test_a2a_endpoint_is_rate_limited():
    """Avaliacao externa (medio prazo, item 1): /a2a agora tem seu
    proprio @limiter.limit("10/minute") (app/a2a/server.py) - antes
    desta mudanca, o endpoint nao tinha NENHUM rate limit (o
    "default_limits" do Limiter so valia para rotas com decorator
    explicito, e SlowAPIMiddleware nunca era registrado)."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tasks/get",
        "params": {"id": "inexistente"},
    }

    for _ in range(10):
        response = client.post("/a2a", json=payload)
        assert response.status_code == 200

    limited = client.post("/a2a", json=payload)
    assert limited.status_code == 429
