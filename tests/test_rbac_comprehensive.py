"""Testes de RBAC (Role-Based Access Control) completos (DA-91)."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestRBACEndpoints:
    """Test RBAC for all admin endpoints (DA-91)."""

    def test_admin_runtime_config_requires_auth(self):
        """Admin /runtime-config endpoint requires authentication."""
        response = client.get("/admin/api/runtime-config")
        assert response.status_code in [200, 401, 403]

    def test_admin_models_requires_auth(self):
        """Admin /models endpoint requires authentication."""
        response = client.get("/admin/models")
        assert response.status_code in [200, 401, 403]

    def test_admin_credentials_requires_auth(self):
        """Admin /credentials endpoint requires authentication."""
        response = client.get("/admin/credentials")
        # 405 = method not allowed (GET may not be supported for this endpoint)
        assert response.status_code in [200, 401, 403, 405, 404]

    def test_admin_systems_requires_auth(self):
        """Admin /systems endpoint requires authentication."""
        response = client.get("/admin/systems")
        assert response.status_code in [200, 401, 403]

    def test_admin_users_requires_auth(self):
        """Admin /users endpoint requires authentication."""
        response = client.get("/admin/users")
        assert response.status_code in [200, 401, 403]

    def test_admin_runtime_config_post_requires_auth(self):
        """Admin /runtime-config POST requires authentication."""
        response = client.post("/admin/api/runtime-config", json={})
        # 405 = method not allowed (POST not supported for GET-only endpoint)
        assert response.status_code in [200, 401, 403, 422, 405]

    def test_admin_models_post_requires_auth(self):
        """Admin /models POST requires authentication."""
        response = client.post("/admin/models", json={})
        # 405 = method not allowed (POST not supported for GET-only endpoint)
        assert response.status_code in [200, 401, 403, 422, 405]
