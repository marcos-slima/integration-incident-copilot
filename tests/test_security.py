"""Security tests: encryption.

IIC-AUD-408: os dois testes pulavam sem LLM_CREDENTIALS_MASTER_KEY no
ambiente - e o CI nunca a define no job de testes, entao nunca rodavam. A
chave agora e gerada por teste (mesmo padrao de tests/test_admin_crypto.py).
"""

import pytest
from cryptography.fernet import Fernet

from app.admin.crypto import decrypt_secret, encrypt_secret
from app.config import settings


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_credentials_master_key", Fernet.generate_key().decode())


def test_fernet_roundtrip():
    """Encrypt/decrypt should be reversible."""
    secret = "sensitive_data_test_12345"

    encrypted = encrypt_secret(secret)
    decrypted = decrypt_secret(encrypted)

    assert decrypted == secret


def test_encryption_produces_different_ciphertext():
    """Same plaintext should produce different ciphertext (random IV)."""
    secret = "same_secret_test"

    enc1 = encrypt_secret(secret)
    enc2 = encrypt_secret(secret)

    assert enc1 != enc2, "Each encryption should use random IV"
