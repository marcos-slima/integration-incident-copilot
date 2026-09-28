"""DA-47 — testes da cifra Fernet de credenciais em repouso.

Cobre: roundtrip, mascara segura, ausencia de plaintext na representacao
gravada, e fail explicito (ConfigurationError) quando a master key falta
ou e invalida — inclusive decifrar com uma key diferente da gravacao.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.admin import crypto
from app.config import settings
from app.exceptions import ConfigurationError


@pytest.fixture
def master_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "llm_credentials_master_key", key)
    return key


def test_roundtrip(master_key):
    secret = "segredo-de-teste-123"
    token = crypto.encrypt_secret(secret)
    assert crypto.decrypt_secret(token) == secret


def test_token_nao_vaza_plaintext(master_key):
    secret = "texto-do-segredo-de-teste"
    token = crypto.encrypt_secret(secret)
    assert token != secret
    assert secret not in token


def test_mask_prefixo_sufixo(master_key):
    assert crypto.mask_secret("abcd1234wxyz") == "abcd****wxyz"
    assert crypto.mask_secret("curta") == "****"
    assert crypto.mask_secret("") == ""


def test_missing_master_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "llm_credentials_master_key", "")
    with pytest.raises(ConfigurationError):
        crypto.encrypt_secret("segredo")


def test_invalid_master_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "llm_credentials_master_key", "nao-eh-fernet-key")
    with pytest.raises(ConfigurationError):
        crypto.encrypt_secret("segredo")


def test_decrypt_with_wrong_key_raises():
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(settings, "llm_credentials_master_key", key_a)
        token = crypto.encrypt_secret("segredo")
        mp.setattr(settings, "llm_credentials_master_key", key_b)
        with pytest.raises(ConfigurationError):
            crypto.decrypt_secret(token)
