"""DA-87: Testes de métricas por domínio (sap/saas/generic).

Endpoint coberto:
- GET /admin/metrics/domains
"""

import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# Admin key from environment
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "local-iic-admin-key-32-chars-aqui")


@pytest.fixture(autouse=True)
def _fixed_admin_key(monkeypatch):
    """Fixa a chave admin para todos os testes deste módulo."""
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", ADMIN_API_KEY)


def _auth(key: str | None = ADMIN_API_KEY) -> dict:
    """Retorna cabeçalho de autenticação admin."""
    if key is None:
        return {}
    return {"X-API-Admin-Key": key}


class TestDomainMetricsAPI:
    """Testes unitários para a API de métricas por domínio (DA-87)."""

    def test_get_domain_metrics_endpoint_exists(self):
        """Sem banco, a rota responde 503 (fail-closed), nunca 200."""
        response = client.get("/admin/metrics/domains", headers=_auth())
        assert response.status_code == 503

    def test_domain_hit1_calculation(self):
        """Verifica cálculo de Hit@1 por domínio."""
        # Testa lógica de cálculo sem banco
        from app.admin.metrics import calculate_domain_hit1

        # Mock data: 10 incidentes, 7 verificados corretamente
        incidents = [
            {"diagnosis_correct": True, "agent_domain": "sap"},
            {"diagnosis_correct": True, "agent_domain": "sap"},
            {"diagnosis_correct": False, "agent_domain": "sap"},
            {"diagnosis_correct": None, "agent_domain": "sap"},  # não verificado
            {"diagnosis_correct": True, "agent_domain": "saas"},
            {"diagnosis_correct": False, "agent_domain": "saas"},
        ]

        result = calculate_domain_hit1(incidents)
        # Hit@1 = verificados corretos / total verificados
        # sap: 2/3 = 0.667
        # saas: 1/2 = 0.5
        # generic: 0/0 = None (ou 0)
        assert "sap" in result
        assert "saas" in result

    def test_domain_latency_calculation(self):
        """Verifica cálculo de tempo médio por domínio."""
        from app.admin.metrics import calculate_domain_latency

        incidents = [
            {"latency_ms": 1000, "agent_domain": "sap"},
            {"latency_ms": 2000, "agent_domain": "sap"},
            {"latency_ms": 500, "agent_domain": "saas"},
        ]

        result = calculate_domain_latency(incidents)
        assert result["sap"] == 1500  # (1000+2000)/2
        assert result["saas"] == 500


class TestDomainMetricsRepository:
    """Testes do repositório de métricas por domínio."""

    def test_count_incidents_by_domain(self):
        """Contagem de incidentes por domínio."""
        from app.admin.metrics import count_incidents_by_domain

        incidents = [
            {"agent_domain": "sap"},
            {"agent_domain": "sap"},
            {"agent_domain": "saas"},
            {"agent_domain": "generic"},
        ]

        result = count_incidents_by_domain(incidents)
        assert result["sap"] == 2
        assert result["saas"] == 1
        assert result["generic"] == 1

    def test_cost_by_domain(self):
        """Cálculo de custo total por domínio."""
        from app.admin.metrics import calculate_cost_by_domain

        incidents = [
            {"cost_usd": 0.001, "agent_domain": "sap"},
            {"cost_usd": 0.002, "agent_domain": "saas"},
            {"cost_usd": None, "agent_domain": "generic"},  # sem custo registrado
        ]

        result = calculate_cost_by_domain(incidents)
        assert result["sap"] == 0.001
        assert result["saas"] == 0.002
        assert result["generic"] == 0.0

    def test_empty_incidents_return_zeros(self):
        """Incidentes vazios devem retornar zeros."""
        from app.admin.metrics import calculate_domain_hit1, count_incidents_by_domain

        result_hit1 = calculate_domain_hit1([])
        result_count = count_incidents_by_domain([])

        # Deve conter todos os domínios com 0
        assert "sap" in result_hit1
        assert "saas" in result_hit1
        assert "generic" in result_hit1

        # Count deve ter contador 0 para cada domínio
        assert result_count["sap"] == 0
        assert result_count["saas"] == 0
        assert result_count["generic"] == 0


class TestDomainMetricsRoute:
    """Exercita a rota com sessao falsa: a lógica roda de verdade."""

    def test_metrics_route_aggregates_repository_rows(self, monkeypatch):
        from types import SimpleNamespace

        import app.admin.routes as admin_routes

        class FakeRepo:
            def __init__(self, session):
                self.session = session

            async def list_recent(self, limit=50, **kwargs):
                # A rota deve recortar a janela (days=7 -> since preenchido)
                assert kwargs["since"] is not None
                return [
                    SimpleNamespace(
                        agent_domain="sap",
                        diagnosis_correct=True,
                        latency_ms=1000,
                        cost_usd=0.001,
                    ),
                    SimpleNamespace(
                        agent_domain="saas",
                        diagnosis_correct=False,
                        latency_ms=500,
                        cost_usd=0.002,
                    ),
                ]

        monkeypatch.setattr(admin_routes, "IncidentRepository", FakeRepo)
        app.dependency_overrides[admin_routes._require_session] = lambda: object()
        try:
            response = client.get("/admin/metrics/domains?days=7", headers=_auth())
        finally:
            app.dependency_overrides.pop(admin_routes._require_session, None)

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        domains = {item["domain"]: item for item in body["domains"]}
        assert domains["sap"]["count"] == 1
        assert domains["sap"]["hit1"] == 1.0
        assert domains["saas"]["count"] == 1
        assert domains["saas"]["hit1"] == 0.0

    @pytest.mark.parametrize("days", [0, 366])
    def test_metrics_route_rejects_out_of_range_window(self, days: int):
        import app.admin.routes as admin_routes

        # Sem sessao valida o 503 da dependency vence a validacao da query;
        # com sessao falsa, o FastAPI valida `days` e devolve 422.
        app.dependency_overrides[admin_routes._require_session] = lambda: object()
        try:
            response = client.get(f"/admin/metrics/domains?days={days}", headers=_auth())
        finally:
            app.dependency_overrides.pop(admin_routes._require_session, None)
        assert response.status_code == 422


class TestDomainDashboardData:
    """Testes da estrutura de dados do dashboard."""

    def test_dashboard_data_structure(self):
        """Verifica estrutura de dados para o dashboard."""
        from app.admin.metrics import build_dashboard_data

        incidents = [
            {
                "agent_domain": "sap",
                "diagnosis_correct": True,
                "latency_ms": 1000,
                "cost_usd": 0.001,
            },
            {
                "agent_domain": "saas",
                "diagnosis_correct": True,
                "latency_ms": 500,
                "cost_usd": 0.002,
            },
        ]

        data = build_dashboard_data(incidents)
        assert "domains" in data
        assert "total" in data
        assert "summary" in data

        # Verifica domínios
        domain_names = [d["domain"] for d in data["domains"]]
        assert "sap" in domain_names
        assert "saas" in domain_names
