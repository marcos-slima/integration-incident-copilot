"""Security tests: audit logging.

IIC-AUD-408: os testes de cifra que existiam aqui eram copia literal de
tests/test_security.py (e pulavam no CI pela falta de master key); ficaram so
la. Este arquivo mantem o que e proprio dele: a trilha de auditoria.
"""


class TestAuditLogging:
    """Test audit logging for compliance."""

    def test_llm_request_recording_exists(self):
        """LLM request recording should exist for audit purposes."""
        from app.admin.repository import record_llm_request

        assert callable(record_llm_request)
