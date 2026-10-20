"""Testes de RBAC da superficie admin (DA-91, IIC-AUD-005).

Os testes antigos aceitavam qualquer um de [200, 401, 403, 404, 405] e
portanto nao podiam falhar. Aqui a chave admin e fixada via monkeypatch
(sem ela, `ADMIN_API_KEY` vazia no conftest faria `compare_digest("", "")`
passar e a rota ficaria aberta) e cada rota exige exatamente 401 sem
credencial. Com credencial valida e sem banco, a resposta e 503
(fail-closed), nunca 200.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)

ADMIN_KEY = "admin-test-key-rbac-02"

# Rotas representativas de cada superficie protegida pelo admin_router
# (prefixo /admin + verify_admin_key). Metodo + path + payload opcional.
PROTECTED_ROUTES = [
    ("GET", "/admin/api/registry/status", None),
    ("GET", "/admin/api/runtime-config", None),
    ("PATCH", "/admin/api/runtime-config", {}),
    ("GET", "/admin/api/effective-config", None),
    ("GET", "/admin/api/models", None),
    ("POST", "/admin/api/models", {}),
    ("GET", "/admin/api/credentials", None),
    ("GET", "/admin/api/systems", None),
    ("POST", "/admin/api/systems", {}),
    ("GET", "/admin/api/users", None),
    ("POST", "/admin/api/users", {}),
    ("GET", "/admin/api/incidents", None),
    ("GET", "/admin/metrics/domains", None),
    ("GET", "/admin/reports/compliance/export?format=pdf", None),
    ("GET", "/admin/dashboard/compliance", None),
    ("GET", "/admin/ontology/hierarchy", None),
]


@pytest.fixture(autouse=True)
def _fixed_admin_key(monkeypatch):
    """Fixa a chave admin: sem isso o valor vazio do conftest abriria a rota."""
    monkeypatch.setattr(settings, "admin_api_key", ADMIN_KEY)


def _auth(key: str = ADMIN_KEY) -> dict[str, str]:
    return {"X-API-Admin-Key": key}


@pytest.mark.parametrize(("method", "path", "payload"), PROTECTED_ROUTES)
def test_admin_surface_requires_admin_key(method: str, path: str, payload: dict | None):
    """Sem X-API-Admin-Key toda rota admin responde 401, nunca 200."""
    response = client.request(method, path, json=payload)
    assert response.status_code == 401, (
        f"{method} {path} sem credencial retornou {response.status_code}; "
        "a rota nao esta protegida por verify_admin_key"
    )


def test_admin_surface_rejects_wrong_key():
    response = client.get("/admin/api/models", headers=_auth("chave-errada"))
    assert response.status_code == 401


def test_valid_key_without_db_is_fail_closed_503():
    """Com a chave correta e sem banco, a rota nao finge sucesso."""
    response = client.get("/admin/api/models", headers=_auth())
    assert response.status_code == 503


def test_removed_deploy_rollback_route_is_not_exposed():
    """DA-82: a rota quebrada nao existe mais (nem com chave admin)."""
    response = client.post(
        "/admin/deploy/rollback",
        headers=_auth(),
        json={"target_schema_hash": "abc"},
    )
    assert response.status_code == 404
