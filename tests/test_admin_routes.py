"""DA-46/47/48 — testes de autenticacao e rotas /admin.

Sem DATABASE_URL (regime dos testes unitarios) as rotas de dados devem
devotar HTTP 503 "Persistencia nao configurada" — nunca mentir sucesso.
A auth (ADMIN_API_KEY) e exercitada isolando uma chave conhecida via
monkeypatch (mesmo padrao de test_api.py para API_KEY).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)

ADMIN_KEY = "admin-test-key-01"


@pytest.fixture(autouse=True)
def _fixed_admin_key(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", ADMIN_KEY)


def _auth(key: str | None = ADMIN_KEY) -> dict:
    if key is None:
        return {}
    return {"X-API-Admin-Key": key}


# --- auth ------------------------------------------------------------------


def test_admin_api_requer_chave():
    r = client.get("/admin/api/registry/status")
    assert r.status_code == 401


def test_admin_api_chave_errada():
    r = client.get("/admin/api/registry/status", headers=_auth("errada"))
    assert r.status_code == 401


def test_registry_status_com_chave_e_flags():
    r = client.get("/admin/api/registry/status", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["managed"] is False
    assert body["db_configured"] is False
    assert body["metering_enabled"] is True
    assert body["master_key_configured"] is False


# --- dados sem banco = fail-closed 503 ------------------------------------


def test_models_list_sem_banco_503():
    r = client.get("/admin/api/models", headers=_auth())
    assert r.status_code == 503


def test_credentials_put_sem_banco_503():
    r = client.put(
        "/admin/api/credentials/api.groq.com",
        headers=_auth(),
        json={"key": "x"},
    )
    assert r.status_code == 503


# --- paginas da UI (shell sem dado; navegacao nao exige header) -----------


def test_ui_index_ok():
    r = client.get("/admin")
    assert r.status_code == 200
    assert "Superfície admin" in r.text


def test_ui_models_ok():
    r = client.get("/admin/models")
    assert r.status_code == 200
    assert "Modelos registrados" in r.text


def test_ui_usage_ok():
    r = client.get("/admin/usage")
    assert r.status_code == 200
    assert "Consumo real de tokens" in r.text
