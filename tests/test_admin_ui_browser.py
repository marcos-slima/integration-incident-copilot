"""Admin UI no Chromium (validacao 2026-10-06, UI-01/N-03).

Marcado `browser` (precisa de Playwright + Chromium; pula quando ausentes). Sobe o app numa
porta local, salva a chave admin pelo proprio formulario e clica em
"Recarregar" de cada pagina: sem DATABASE_URL a API responde 503 e a pagina
precisa mostrar essa mensagem - prova de que o JS executou sob a CSP.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

pytestmark = pytest.mark.browser

playwright = pytest.importorskip("playwright.sync_api")

_KEY = "chave-admin-teste-" + "x" * 16
_PAGES = [
    ("/admin/models", "loadModels"),
    ("/admin/systems", "loadSystems"),
    ("/admin/usage", "loadUsage"),
    ("/admin/users", "loadUsers"),
    ("/admin/web-search", "loadSources"),
    ("/admin/incidents", "loadIncidents"),
]


@pytest.fixture(scope="module")
def base_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(
        os.environ,
        DATABASE_URL="",
        ADMIN_API_KEY=_KEY,
        API_KEY="k" * 32,
        LANGFUSE_TRACING_ENABLED="false",
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(180):
        try:
            urllib.request.urlopen(url + "/health", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(1)
    else:
        proc.terminate()
        pytest.skip("app nao subiu")
    yield url
    proc.terminate()


@pytest.fixture(scope="module")
def browser():
    try:
        with playwright.sync_playwright() as p:
            # PLAYWRIGHT_CHROMIUM_EXECUTABLE: Chromium do sistema quando a versao
            # baixada pelo Playwright nao bate com o pacote instalado.
            b = p.chromium.launch(
                executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
            )
            yield b
            b.close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Chromium indisponivel: {exc}")


@pytest.mark.parametrize(("path", "reload_fn"), _PAGES)
def test_pagina_executa_js_sob_csp(base_url, browser, path, reload_fn):
    page = browser.new_page()
    erros: list[str] = []
    page.on("pageerror", lambda e: erros.append(str(e)))
    page.on(
        "console", lambda m: erros.append(m.text) if "Content Security Policy" in m.text else None
    )
    page.goto(base_url + path)
    page.fill("#adminkey", _KEY)
    page.click('[data-click="saveKey"]')
    page.wait_for_load_state("networkidle")
    page.click(f'[data-click="{reload_fn}"]')
    page.wait_for_function("document.getElementById('msg').textContent.length > 0")
    assert "DATABASE_URL" in page.text_content("#msg")
    assert erros == []
    page.close()
