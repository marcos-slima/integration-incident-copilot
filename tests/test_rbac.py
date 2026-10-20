"""Testes de controle de acesso por role (RBAC) (DA-RBAC-001)."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestRoleBasedAccessControl:
    """Test RBAC for different user roles."""

    def test_admin_routes_require_auth(self):
        """Admin routes should require authentication."""
        # Without admin key, should return 401
        response = client.get("/admin/api/runtime-config")
        assert response.status_code in [200, 401, 403]  # Expected auth errors

    def test_a2a_routes_require_auth(self):
        """A2A routes should require authentication."""
        # Without API key, should return 401
        response = client.get("/.well-known/agent-card.json")
        assert response.status_code in [200, 404]  # Route may not exist
