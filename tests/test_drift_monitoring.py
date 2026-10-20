"""Testes de monitors de drift para modelo e dados (DA-MONITOR-001)."""


class TestDriftMonitoring:
    """Test drift detection for models and data."""

    def test_latency_tracking_exists(self):
        """Latency tracking must exist for model performance monitoring."""
        from app.admin.repository import record_call_latency

        assert record_call_latency is not None
        assert callable(record_call_latency)

    def test_usage_metrics_exist(self):
        """Usage metrics must be recorded for drift detection."""
        from app.admin.repository import record_llm_request

        assert record_llm_request is not None
        assert callable(record_llm_request)

    def test_cost_tracking_exists(self):
        """Cost tracking must exist for drift detection."""
        from app.admin.repository import record_llm_cost

        assert record_llm_cost is not None
        assert callable(record_llm_cost)


class TestConfidenceDrift:
    """Test confidence score drift detection."""

    def test_confidence_metrics_exist(self):
        """Confidence metrics must be recorded for drift detection."""

        from app.models import DiagnosisResponse

        # Verify the model has confidence fields via Pydantic model_fields
        fields = DiagnosisResponse.model_fields
        assert "diagnosis_confidence" in fields
        assert "model_confidence" in fields
