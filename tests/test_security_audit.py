"""Security tests: encryption, role-based access control (RBAC), audit logging."""

import pytest

from app.admin.crypto import decrypt_secret, encrypt_secret
from app.config import settings


def test_fernet_roundtrip():
    """Encrypt/decrypt should be reversible."""
    if not settings.llm_credentials_master_key:
        pytest.skip("LLM_CREDENTIALS_MASTER_KEY not set")

    secret = "sensitive_data_test_12345"

    encrypted = encrypt_secret(secret)
    decrypted = decrypt_secret(encrypted)

    assert decrypted == secret


def test_encryption_produces_different_ciphertext():
    """Same plaintext should produce different ciphertext (random IV)."""
    if not settings.llm_credentials_master_key:
        pytest.skip("LLM_CREDENTIALS_MASTER_KEY not set")

    secret = "same_secret_test"

    enc1 = encrypt_secret(secret)
    enc2 = encrypt_secret(secret)

    assert enc1 != enc2, "Each encryption should use random IV"


class TestAuditLogging:
    """Test audit logging for compliance."""

    def test_llm_request_recording_exists(self):
        """LLM request recording should exist for audit purposes."""
        from app.admin.repository import record_llm_request

        assert record_llm_request is not None
