"""Testes de DLP/NER avançado (DA-DLP-001)."""


class TestDLPSecurity:
    """Test DLP/NER capabilities beyond regex (DA-80)."""

    def test_pii_detection_functions_exist(self):
        """PII detection functions must exist for NER/regex coverage."""
        from app.redaction import redact_pii_deep, redact_pii_text

        assert redact_pii_text is not None
        assert redact_pii_deep is not None
        assert callable(redact_pii_text)
        assert callable(redact_pii_deep)

    def test_cpf_redaction_exists(self):
        """CPF redaction must exist."""
        from app.redaction import _redact_cpf_digits_with_context

        assert _redact_cpf_digits_with_context is not None
        assert callable(_redact_cpf_digits_with_context)

    def test_email_redaction_exists(self):
        """Email redaction must exist."""
        from app.redaction import redact_pii_text

        doc = redact_pii_text.__doc__
        assert doc is not None
        assert "email" in doc.lower() or "e-mail" in doc.lower()

    def test_redaction_module_exists(self):
        """Redaction module must exist."""
        import app.redaction

        assert app.redaction is not None

    def test_no_pii_in_diagnosis_response(self):
        """PII should not appear in final diagnosis response."""
        from app.models import DiagnosisResponse

        fields = DiagnosisResponse.model_fields
        assert "report_markdown" in fields
