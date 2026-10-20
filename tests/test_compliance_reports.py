"""DA-89: Testes de relatórios compliance (export PDF/XLSX).

Endpoint: GET /admin/reports/compliance/export?format=pdf|xlsx
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


class TestComplianceReportsAPI:
    """Testes unitários para a API de relatórios compliance (DA-89)."""

    def test_get_compliance_export_pdf_endpoint_exists(self):
        """Sem banco, o export responde 503 (fail-closed), nunca 200."""
        response = client.get("/admin/reports/compliance/export?format=pdf", headers=_auth())
        assert response.status_code == 503

    def test_get_compliance_export_xlsx_endpoint_exists(self):
        """Sem banco, o export responde 503 (fail-closed), nunca 200."""
        response = client.get("/admin/reports/compliance/export?format=xlsx", headers=_auth())
        assert response.status_code == 503

    def test_unknown_format_returns_422(self):
        """Formato desconhecido deve retornar 422 Unprocessable Entity."""
        response = client.get("/admin/reports/compliance/export?format=csv", headers=_auth())
        assert response.status_code == 422

    def test_missing_format_returns_422(self):
        """Falta de parâmetro format deve retornar 422."""
        response = client.get("/admin/reports/compliance/export", headers=_auth())
        assert response.status_code == 422


class TestComplianceExportRoute:
    """Exercita a rota com dados falsos: exporta de verdade, sem banco."""

    @staticmethod
    def _fake_data():
        async def fake_data(session, days=7):
            return {
                "incidents": [
                    {
                        "id": "123e4567-e89b-12d3-a456-426614174000",
                        "created_at": "2026-10-20T10:00:00Z",
                        "agent_domain": "sap",
                        "probable_root_cause": "Timeout de resposta",
                        "model_confidence": 0.95,
                        "diagnosis_correct": True,
                        "cost_usd": 0.001,
                    }
                ],
                "generated_at": "2026-10-20T10:00:00Z",
            }

        return fake_data

    def _get_with_fake_db(self, monkeypatch, format_: str):
        from app.admin import reports
        from app.db import get_db_session

        monkeypatch.setattr(reports, "get_compliance_data", self._fake_data())
        app.dependency_overrides[get_db_session] = lambda: object()
        try:
            return client.get(
                f"/admin/reports/compliance/export?format={format_}&days=7",
                headers=_auth(),
            )
        finally:
            app.dependency_overrides.pop(get_db_session, None)

    def test_pdf_export_route_returns_pdf(self, monkeypatch):
        response = self._get_with_fake_db(monkeypatch, "pdf")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/pdf")
        assert response.content.startswith(b"%PDF")

    def test_xlsx_export_route_returns_xlsx(self, monkeypatch):
        response = self._get_with_fake_db(monkeypatch, "xlsx")
        assert response.status_code == 200
        assert "spreadsheetml" in response.headers["content-type"]
        assert response.content.startswith(b"PK")


class TestComplianceReportData:
    """Testes de preparação de dados para relatórios."""

    def test_compliance_summary_structure(self):
        """Verifica estrutura do resumo de compliance."""
        from app.admin.reports import build_compliance_summary

        # Mock data
        incidents = [
            {
                "created_at": "2026-10-20T10:00:00Z",
                "agent_domain": "sap",
                "diagnosis_correct": True,
                "cost_usd": 0.001,
            },
        ]

        summary = build_compliance_summary(incidents)
        assert "total_incidents" in summary
        assert "verified_correctly" in summary
        assert "verification_rate" in summary
        assert "cost_usd_total" in summary
        assert "by_domain" in summary

    def test_quality_metrics_calculation(self):
        """Verifica cálculo de métricas de qualidade."""
        from app.admin.reports import calculate_quality_metrics

        incidents = [
            {"diagnosis_correct": True, "agent_domain": "sap"},
            {"diagnosis_correct": False, "agent_domain": "sap"},
            {"diagnosis_correct": True, "agent_domain": "saas"},
            {"diagnosis_correct": None, "agent_domain": "saas"},  # não verificado
        ]

        metrics = calculate_quality_metrics(incidents)
        assert "overall_accuracy" in metrics
        assert "by_domain" in metrics
        assert "verification_rate" in metrics

        # Accuracy = 2/3 (um não verificado não conta)
        assert metrics["overall_accuracy"] == pytest.approx(0.6667, abs=0.001)

    def test_auditable_events_extraction(self):
        """Extrai eventos auditáveis do histórico de incidentes."""
        from app.admin.reports import extract_auditable_events

        incidents = [
            {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "created_at": "2026-10-20T10:00:00Z",
                "agent_domain": "sap",
                "probable_root_cause": "Timeout de resposta",
                "model_confidence": 0.95,
                "diagnosis_correct": True,
            },
        ]

        events = extract_auditable_events(incidents)
        assert len(events) == 1
        assert events[0]["incident_id"] == "123e4567-e89b-12d3-a456-426614174000"
        assert "timestamp" in events[0]
        assert "action" in events[0]

    def test_empty_incidents_returns_zeroes(self):
        """Incidentes vazios devem retornar estrutura válida com zeros."""
        from app.admin.reports import build_compliance_summary, calculate_quality_metrics

        summary = build_compliance_summary([])
        assert summary["total_incidents"] == 0
        assert summary["cost_usd_total"] == 0.0

        metrics = calculate_quality_metrics([])
        assert metrics["overall_accuracy"] == 0.0
        assert metrics["verification_rate"] == 0.0


class TestComplianceExportPDF:
    """Testes de exportação PDF."""

    def test_pdf_export_returns_bytes(self):
        """Exportação PDF deve retornar bytes."""
        from app.admin.reports import export_pdf

        data = {
            "summary": {"total_incidents": 10, "cost_usd_total": 0.1},
            "metrics": {"overall_accuracy": 0.9},
        }

        pdf_bytes = export_pdf(data)
        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0
        # PDF magic number
        assert pdf_bytes.startswith(b"%PDF")

    def test_pdf_contains_expected_sections(self):
        """PDF deve conter seções esperadas."""
        from app.admin.reports import export_pdf

        data = {
            "summary": {"total_incidents": 5, "cost_usd_total": 0.05},
            "metrics": {"overall_accuracy": 0.8},
            "by_domain": {"sap": {"count": 3}, "saas": {"count": 2}},
            "auditable_events": [],
        }

        pdf_bytes = export_pdf(data)
        # Conversão para string para verificar conteúdo (aproximado)
        content = pdf_bytes.decode("latin-1", errors="ignore")
        assert "Compliance Report" in content or "Incident" in content


class TestComplianceExportXLSX:
    """Testes de exportação XLSX."""

    def test_xlsx_export_returns_bytes(self):
        """Exportação XLSX deve retornar bytes."""
        from app.admin.reports import export_xlsx

        data = {
            "summary": {"total_incidents": 10, "cost_usd_total": 0.1},
            "metrics": {"overall_accuracy": 0.9},
        }

        xlsx_bytes = export_xlsx(data)
        assert isinstance(xlsx_bytes, bytes)
        assert len(xlsx_bytes) > 0
        # XLSX é um ZIP
        assert xlsx_bytes.startswith(b"PK")

    def test_xlsx_structure(self):
        """XLSX deve ter estrutura de planilhas."""
        import io

        from app.admin.reports import export_xlsx

        data = {
            "summary": {"total_incidents": 3, "cost_usd_total": 0.03},
            "by_domain": {
                "sap": {"count": 2, "accuracy": 1.0},
                "saas": {"count": 1, "accuracy": 0.5},
            },
        }

        xlsx_bytes = export_xlsx(data)
        # Abre como ZIP e verifica sheets
        import zipfile

        with zipfile.ZipFile(io.BytesIO(xlsx_bytes), "r") as zf:
            # Deve ter pelo menos uma planilha
            sheet_names = [name for name in zf.namelist() if name.endswith(".xml")]
            assert len(sheet_names) >= 1


class TestComplianceReportRepository:
    """Testes do repositório para relatórios."""

    @pytest.mark.asyncio
    async def test_get_compliance_data_empty(self):
        """Busca sem incidentes deve retornar estrutura vazia."""
        from app.admin.reports import get_compliance_data

        class MockSession:
            pass

        mock_session = MockSession()
        data = await get_compliance_data(mock_session, days=7)

        assert "incidents" in data
        assert data["incidents"] == []
