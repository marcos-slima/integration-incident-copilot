"""DA-46 — testes do modo gerenciado (LLM_REGISTRY_DB): fail-closed.

O registro dirige o runtime apenas quando o operador liga a flag. Nos
testes unitarios (sem banco), a consequencia observada e a rejeicao
explícita (ConfigurationError) — nunca queda silenciosa para o .env.
Com flag OFF, o comportamento do factory permanece inalterado.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.exceptions import ConfigurationError
from app.llm.factory import get_chat_model


def test_fail_closed_sem_registro_quando_managed(monkeypatch):
    monkeypatch.setattr(settings, "llm_registry_db", True)
    monkeypatch.setattr(settings, "database_url", "")
    with pytest.raises(ConfigurationError, match="llm_registry_db=true"):
        get_chat_model()


def test_flag_off_mantem_comportamento_padrao(monkeypatch):
    monkeypatch.setattr(settings, "llm_registry_db", False)
    llm = get_chat_model()
    assert getattr(llm, "model", "")  # instanciou um ChatOllama local default
