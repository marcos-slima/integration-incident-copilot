"""DA-60 - evidence_json cifrado em repouso (validacao 2026-10-06, N-06/PRIV-01).

Antes: sem master key a evidencia virava None em silencio (log dizia
"deixando em claro"), e com chave a PII ia inteira para dentro do cifrado
e voltava decifrada na API admin.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.admin import crypto
from app.config import settings
from app.exceptions import ConfigurationError

_EVID = [
    {
        "source": "user:description",
        "trust_level": "user_reported",
        "excerpt": "Cliente joao.silva@empresa.com CPF 123.456.789-09 sem pedido",
    }
]


@pytest.fixture
def chave(monkeypatch):
    k = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "llm_credentials_master_key", k)
    return k


@pytest.fixture
def sem_chave(monkeypatch):
    monkeypatch.setattr(settings, "llm_credentials_master_key", "")


def test_cifra_e_redige_pii_antes(chave):
    token = crypto.encrypt_evidence(_EVID)
    assert token.startswith("gAAA")
    assert "joao" not in token
    volta = crypto.decrypt_evidence(token)
    texto = volta[0]["excerpt"]
    assert "joao.silva@empresa.com" not in texto and "123.456.789-09" not in texto
    assert "[EMAIL_REDACTED]" in texto and "[CPF_REDACTED]" in texto


def test_sem_chave_falha_explicitamente(sem_chave):
    with pytest.raises(ConfigurationError):
        crypto.encrypt_evidence(_EVID)


def test_none_continua_none(sem_chave):
    assert crypto.encrypt_evidence(None) is None
    assert crypto.decrypt_evidence(None) is None


def test_decrypt_legado_lista_e_json_texto(chave):
    assert crypto.decrypt_evidence(_EVID) == _EVID
    assert crypto.decrypt_evidence('[{"a": 1}]') == [{"a": 1}]


def test_decrypt_com_chave_errada_levanta(chave, monkeypatch):
    token = crypto.encrypt_evidence(_EVID)
    monkeypatch.setattr(settings, "llm_credentials_master_key", Fernet.generate_key().decode())
    with pytest.raises(ConfigurationError, match="mesma usada na gravacao"):
        crypto.decrypt_evidence(token)


def test_build_incident_row_nao_perde_evidencia_em_silencio(sem_chave):
    """Regressao R10/N-06: sem chave a linha nao e montada (o recorder loga o
    erro) em vez de gravar evidence_json=None como se estivesse tudo bem."""
    from app.agent.nodes import _assemble_evidence
    from app.models import DiagnosisResponse, Evidence, IncidentRequest
    from app.services.incident_recorder import build_incident_row

    req = IncidentRequest(description=_EVID[0]["excerpt"])
    resp = DiagnosisResponse(
        probable_root_cause="x",
        model_confidence=0.1,
        diagnosis_confidence=0.1,
        next_steps=[],
        report_markdown="",
        evidence=[Evidence(**e) for e in _assemble_evidence({"description": req.description})],
    )
    with pytest.raises(ConfigurationError):
        build_incident_row(
            incident_id="00000000-0000-0000-0000-000000000001",
            request=req,
            response=resp,
            final_state={"description": "x"},
            latency_ms=1,
        )


def test_boot_exige_chave_quando_ha_banco(monkeypatch, sem_chave):
    from app import main

    monkeypatch.setattr(settings, "database_url", "postgresql://u@h/db")
    with pytest.raises(ConfigurationError, match="LLM_CREDENTIALS_MASTER_KEY"):
        main._ensure_evidence_key_configured()
    monkeypatch.setattr(settings, "database_url", "")
    main._ensure_evidence_key_configured()  # sem banco: nada exigido
