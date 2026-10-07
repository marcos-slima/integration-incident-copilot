"""Validacao 2026-10-07, Bloco 3: notificacoes, cobertura e A2A (M-12, M-13, M-28)."""

from __future__ import annotations

import asyncio

import pytest

from app.models import DiagnosisResponse

# ---------------------------------------------------------------------------
# M-12: e-mail de ativacao usa o provedor configurado
# ---------------------------------------------------------------------------


def test_m12_deliver_email_usa_mailpit_quando_configurado(monkeypatch):
    import smtplib

    from app import webusers
    from app.config import settings

    enviados = []

    class _SMTP:
        def __init__(self, host, port, timeout=None):
            enviados.append((host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def send_message(self, msg):
            enviados.append(msg["To"])

    monkeypatch.setattr(smtplib, "SMTP", _SMTP)
    monkeypatch.setattr(settings, "email_provider", "mailpit")
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_port", 1025)
    monkeypatch.setattr(settings, "smtp_from", "noreply@example.com")
    result = webusers.deliver_email("ana@example.com", "Token de ativacao: x")
    assert result.delivered is True
    assert enviados == [("mailpit", 1025, 10), "ana@example.com"]


def test_m12_falha_de_envio_continua_out_of_band(monkeypatch):
    import smtplib

    from app import webusers
    from app.config import settings

    def _quebra(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(smtplib, "SMTP", _quebra)
    monkeypatch.setattr(settings, "email_provider", "mailpit")
    assert webusers.deliver_email("ana@example.com", "x").delivered is False


def test_m12_sem_provedor_e_out_of_band(monkeypatch):
    from app import webusers
    from app.config import settings

    monkeypatch.setattr(settings, "email_provider", "")
    assert webusers.deliver_email("ana@example.com", "x").delivered is False


def test_m12_modulo_quebrado_removido():
    import importlib.util

    assert importlib.util.find_spec("app.notifications.service") is None


# ---------------------------------------------------------------------------
# M-13: erro de dado da cobertura vira CoverageError, nao UnboundLocalError
# ---------------------------------------------------------------------------


def test_m13_valor_invalido_vira_coverage_error():
    from app.evaluation.coverage import CoverageError, _as_cell

    with pytest.raises(CoverageError, match="S/4HANA/odata"):
        _as_cell(42, "S/4HANA", "odata")


# ---------------------------------------------------------------------------
# M-28: A2A assincrono, erro opaco e campos estruturados preservados
# ---------------------------------------------------------------------------


def _resposta():
    return DiagnosisResponse(
        probable_root_cause="x",
        model_confidence=0.5,
        diagnosis_confidence=0.5,
        next_steps=[],
        report_markdown="r",
    )


def _mensagem(**dados):
    return {
        "role": "user",
        "parts": [{"kind": "text", "text": "IDoc 51"}, {"kind": "data", "data": dados}],
    }


def test_m28_campos_estruturados_chegam_ao_pipeline():
    from app.a2a.task_manager import TaskManager
    from app.a2a.task_store import InMemoryTaskStore

    recebidos = []

    def diag(request):
        recebidos.append(request)
        return _resposta()

    manager = TaskManager(diagnosis_fn=diag, task_store=InMemoryTaskStore())
    asyncio.run(
        manager.handle_message(
            _mensagem(
                interface_type="rfc",
                connector_source_system="S4H-PRD",
                sensitivity_level="confidential",
                pii_detected=True,
            )
        )
    )
    req = recebidos[0]
    assert (req.connector_source_system, req.sensitivity_level, req.pii_detected) == (
        "S4H-PRD",
        "confidential",
        True,
    )


def test_m28_validacao_nao_ecoa_o_valor():
    from app.a2a.task_manager import TaskManager
    from app.a2a.task_store import InMemoryTaskStore

    manager = TaskManager(diagnosis_fn=lambda r: _resposta(), task_store=InMemoryTaskStore())
    task = asyncio.run(manager.handle_message(_mensagem(sensitivity_level="cpf-123.456.789-09")))
    assert task.state == "failed"
    assert "123.456.789-09" not in task.error and "sensitivity_level" in task.error


def test_m28_modo_nao_bloqueante():
    import threading

    from app.a2a.task_manager import TaskManager
    from app.a2a.task_store import InMemoryTaskStore

    liberar = threading.Event()

    def diag(request):
        liberar.wait(5)
        return _resposta()

    manager = TaskManager(diagnosis_fn=diag, task_store=InMemoryTaskStore())

    async def cenario():
        task = await manager.handle_message(_mensagem(), blocking=False)
        assert task.state == "working"
        liberar.set()
        for _ in range(100):
            await asyncio.sleep(0.02)
            if manager.get_task(task.id).state == "completed":
                return True
        return False

    assert asyncio.run(cenario()) is True
