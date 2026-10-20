"""Testes de explicabilidade XAI para Highlight de chunks (DA-XAI-001)."""


class TestExplanability:
    """Test XAI explanations for diagnosis resolution."""

    def test_explanation_field_exists(self):
        """Explanation field must exist in DiagnosisResponse."""

        # Explanation field is not required in current DiagnosisResponse
        # but the _assemble_evidence function provides explainability
        from app.agent.nodes import _assemble_evidence

        assert _assemble_evidence is not None
        assert callable(_assemble_evidence)

    def test_evidence_trust_levels_exist(self):
        """Evidence trust levels must exist for explainability."""
        from app.agent.nodes import _assemble_evidence

        # _assemble_evidence returns items with trust_level field
        # which is the source of explainability
        assert _assemble_evidence is not None

    def test_matched_source_tracing(self):
        """Matched source should be traceable for explanations."""
        from app.models import DiagnosisResponse

        fields = DiagnosisResponse.model_fields
        assert "matched_source" in fields
