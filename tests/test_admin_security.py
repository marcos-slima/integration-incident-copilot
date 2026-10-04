"""DA-46/47/48 — testes da auth admin (verify_admin_key/ensure).

Mesma semantica da DA-18 aplicada a superficie admin: chave vazia no
startup NAO significa auth desabilitada — ensure_admin_key_configured
gera chave efemera; a comparacao usa compare_digest.
"""

from __future__ import annotations

import fastapi
import pytest

from app.admin.security import ensure_admin_key_configured, verify_admin_key
from app.config import settings


@pytest.mark.parametrize("chave", [None, "", "errada"])
def test_rejeita_sem_chave_correta(monkeypatch, chave):
    monkeypatch.setattr(settings, "admin_api_key", "admin-key-estavel")
    with pytest.raises(fastapi.HTTPException) as exc:
        verify_admin_key(chave)
    assert exc.value.status_code == 401


def test_aceita_chave_correta(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "admin-key-estavel")
    # nao levanta
    verify_admin_key("admin-key-estavel")


def test_ensure_gera_quando_vazia(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "")
    ensure_admin_key_configured()
    assert settings.admin_api_key  # nao-vazio


def test_ensure_mantem_quando_fixa(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "admin-key-fixa")
    ensure_admin_key_configured()
    assert settings.admin_api_key == "admin-key-fixa"
