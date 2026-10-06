"""Admin UI sob CSP estrita (validacao 2026-10-06, UI-01/N-03).

A CSP das paginas e `script-src 'self'`: o navegador bloqueia <script>
inline e atributos on*. Antes desta correcao os templates tinham os dois e a
UI ficava inerte sem erro visivel (nada executava). Estes testes travam o
contrato estaticamente; tests/test_admin_ui_browser.py exercita no Chromium.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

_ADMIN = Path(__file__).resolve().parents[1] / "app" / "admin"
_TEMPLATES = sorted((_ADMIN / "templates").glob("*.html"))
_STATIC_JS = sorted((_ADMIN / "static").glob("*.js"))
_INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=\s*[\"']", re.IGNORECASE)

client = TestClient(app)


@pytest.mark.parametrize("tpl", _TEMPLATES, ids=lambda p: p.name)
def test_template_sem_script_inline(tpl: Path) -> None:
    html = tpl.read_text(encoding="utf-8")
    for tag in re.findall(r"<script\b[^>]*>", html, flags=re.IGNORECASE):
        assert "src=" in tag, f"{tpl.name}: <script> inline seria bloqueado pela CSP: {tag}"


@pytest.mark.parametrize("path", _TEMPLATES + _STATIC_JS, ids=lambda p: p.name)
def test_sem_handler_on_atributo(path: Path) -> None:
    assert not _INLINE_HANDLER.search(path.read_text(encoding="utf-8")), (
        f"{path.name}: atributo on*= e bloqueado pela CSP; use data-click/data-submit"
    )


def _funcoes_definidas() -> set[str]:
    nomes: set[str] = set()
    for js in _STATIC_JS:
        nomes |= set(
            re.findall(r"^function\s+(\w+)\s*\(", js.read_text(encoding="utf-8"), re.MULTILINE)
        )
    return nomes


def test_todo_data_click_tem_funcao() -> None:
    definidas = _funcoes_definidas()
    usados: set[str] = set()
    for path in _TEMPLATES + _STATIC_JS:
        texto = path.read_text(encoding="utf-8")
        usados |= set(re.findall(r'data-(?:click|submit)="(\w+)"', texto))
    assert usados, "nenhum data-click encontrado - o teste nao esta vendo os templates"
    assert usados <= definidas, f"handlers sem funcao: {sorted(usados - definidas)}"


@pytest.mark.parametrize(
    "path", ["/admin", "/admin/models", "/admin/users", "/admin/incidents", "/admin/web-search"]
)
def test_pagina_admin_tem_csp_estrita(path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy", "")
    assert (
        "script-src 'self'" in csp
        and "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
    )
    for src in re.findall(r'<script[^>]+src="([^"]+)"', r.text):
        assert client.get(src).status_code == 200, f"{path}: script {src} nao e servido"


def test_docs_fora_da_csp() -> None:
    # Swagger carrega de CDN + script inline; com a CSP estrita fica em branco.
    assert "content-security-policy" not in client.get("/docs").headers
