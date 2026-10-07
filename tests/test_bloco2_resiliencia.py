"""Bloco 2 (validacao 2026-10-07): R01 e semaforo do grafo (REL-01)."""

from __future__ import annotations

import threading
import time

import httpx
import pytest

from app.config import settings
from app.exceptions import DiagnosisOverloadedError, DiagnosisTimeoutError
from app.llm.factory import TRANSPORT_FAILURE_EXCEPTIONS


def test_r01_excecoes_do_sdk_openai_acionam_fallback() -> None:
    openai = pytest.importorskip("openai")
    req = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    casos = [
        openai.APIConnectionError(request=req),
        openai.APITimeoutError(request=req),
        openai.InternalServerError("502", response=httpx.Response(502, request=req), body=None),
        httpx.ReadError("conexao caiu no meio da resposta", request=req),
    ]
    for exc in casos:
        assert isinstance(exc, TRANSPORT_FAILURE_EXCEPTIONS), type(exc).__name__


def test_r01_erro_de_cliente_nao_vira_fallback() -> None:
    openai = pytest.importorskip("openai")
    req = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    exc = openai.AuthenticationError("401", response=httpx.Response(401, request=req), body=None)
    assert not isinstance(exc, TRANSPORT_FAILURE_EXCEPTIONS)


def test_semaforo_so_libera_quando_a_thread_termina(monkeypatch) -> None:
    """Antes: timeout liberava a vaga com o grafo ainda rodando, e o limite
    de concorrencia deixava de valer. Agora a 5a chamada recebe 503
    (DiagnosisOverloadedError) enquanto 4 diagnosticos presos ainda rodam."""
    import app.agent.graph as g

    libera = threading.Event()

    class _Grafo:
        def invoke(self, state):
            libera.wait(10)
            return state

    monkeypatch.setattr(g, "get_graph", lambda: _Grafo())
    monkeypatch.setattr(settings, "diagnosis_timeout_seconds", 0.05)
    monkeypatch.setattr(g, "_graph_invoke_semaphore", threading.Semaphore(4))

    for _ in range(4):
        with pytest.raises(DiagnosisTimeoutError) as info:
            g._invoke_graph_with_timeout({"description": "x"})
        assert not isinstance(info.value, DiagnosisOverloadedError)
    with pytest.raises(DiagnosisOverloadedError):
        g._invoke_graph_with_timeout({"description": "x"})

    libera.set()
    deadline = time.time() + 5
    while time.time() < deadline and g._graph_invoke_semaphore._value < 4:
        time.sleep(0.02)
    assert g._graph_invoke_semaphore._value == 4
    monkeypatch.setattr(settings, "diagnosis_timeout_seconds", 5)
    assert g._invoke_graph_with_timeout({"description": "ok"}) == {"description": "ok"}


def test_sobrecarga_vira_503_com_retry_after(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app import main

    monkeypatch.setattr(settings, "api_key", "k" * 32)

    def _cheio(*a, **k):
        raise DiagnosisOverloadedError("cheio")

    monkeypatch.setattr(main, "run_diagnosis", _cheio)
    r = TestClient(main.app).post(
        "/diagnose", json={"description": "IDoc travado"}, headers={"X-API-Key": "k" * 32}
    )
    assert r.status_code == 503 and r.headers.get("retry-after") == "10"
