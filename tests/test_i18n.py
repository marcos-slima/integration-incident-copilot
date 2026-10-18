"""DA-73: i18n das UIs - negociacao do idioma, catalogos, Admin UI no idioma
do navegador, erros com `code` e o gate `ui_i18n`."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.i18n import (
    embed_json,
    load_catalog,
    negotiate,
    parse_accept_language,
    translate,
)
from app.i18n.checks import parity_problems, ui_i18n_problems
from app.i18n.inventory import scan_literals, scan_markup
from app.main import app

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


# --- negociacao -------------------------------------------------------------


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"accept_language": "pt-BR,pt;q=0.9,en;q=0.8"}, "pt-BR"),
        ({"accept_language": "pt-PT"}, "pt-BR"),  # idioma base
        ({"accept_language": "en-GB,en;q=0.9"}, "en"),
        ({"accept_language": "fr-FR,de;q=0.9"}, "en"),  # sem suporte -> fallback
        ({"accept_language": "fr;q=0.9,pt;q=0.5"}, "pt-BR"),  # pula o nao suportado
        ({"accept_language": "en;q=0.4,pt-BR;q=0.8"}, "pt-BR"),  # ordem por q
        ({"cookie": "en", "accept_language": "pt-BR"}, "en"),  # escolha explicita vence
        ({"query": "pt-BR", "cookie": "en"}, "pt-BR"),
        ({"cookie": "xx"}, "xx"),  # pseudo-locale so explicito
        ({"accept_language": "xx"}, "en"),
        ({}, "en"),
    ],
)
def test_negotiate(kw, expected):
    assert negotiate(**kw) == expected


def test_accept_language_ignores_q_zero_and_garbage():
    assert parse_accept_language("pt;q=0, en;q=abc, de") == ["de"]


# --- catalogo ----------------------------------------------------------------


def test_translate_interpolates_and_falls_back():
    assert translate("admin", "pt-BR", "usage.confirmReset", model="m1").endswith("m1?")
    assert translate("admin", "en", "nao.existe") == "nao.existe"
    assert translate("admin", "xx", "nav.home") == "⟦Home⟧"


def test_embed_json_cannot_close_the_script_tag():
    out = str(embed_json({"a": "</script><script>alert(1)</script> & <b>"}))
    assert "<" not in out and ">" not in out and "&" not in out
    assert json.loads(out) == {"a": "</script><script>alert(1)</script> & <b>"}


def test_real_catalogs_are_consistent_and_complete():
    assert ui_i18n_problems(ROOT) == []


def test_parity_detects_missing_key_and_variable_mismatch():
    problems = parity_problems(
        {"pt-BR": {"a": "x {{n}}", "b": "y"}, "en": {"a": "x {{count}}"}}, "admin"
    )
    assert any("'b' falta em en" in p for p in problems)
    assert any("variaveis de 'a'" in p for p in problems)


def test_inventory_flags_hardcoded_text_and_honors_ignore():
    jsx = "<h1>Texto fixo</h1>\n<p>{t('a.b')}</p>\n<input placeholder=\"Digite aqui\" />\n"
    found = [f.text for f in scan_markup(Path("x.tsx"), jsx, jinja=False)]
    assert "Texto fixo" in found and 'placeholder="Digite aqui"' in found
    js = "setError('Falha ao salvar o modelo');\nconst k = 'status-badge status-ok';\nx('Sem acento ok'); // i18n-ignore\n"
    found_js = [f.text for f in scan_literals(Path("x.js"), js)]
    assert found_js == ["Falha ao salvar o modelo"]
    jinja = "<h2>{{ t('a.b') }}</h2>\n<p>Fixo no template</p>\n{% if x %}<b>odata</b>{% endif %}\n"
    assert [f.text for f in scan_markup(Path("x.html"), jinja, jinja=True)] == ["Fixo no template"]


# --- Admin UI no idioma do navegador -----------------------------------------


def _catalog_from(html: str) -> dict:
    m = re.search(
        r'<script type="application/json" id="i18n-catalog">(.*?)</script>', html, re.DOTALL
    )
    assert m, "catalogo embutido ausente"
    return json.loads(m.group(1))


def test_admin_page_follows_accept_language():
    en = client.get("/admin/models", headers={"Accept-Language": "en-US,en;q=0.9"})
    pt = client.get("/admin/models", headers={"Accept-Language": "pt-BR"})
    assert '<html lang="en">' in en.text and "Registered models (by origin)" in en.text
    assert '<html lang="pt-BR">' in pt.text and "Modelos registrados (por origem)" in pt.text
    assert en.headers["content-language"] == "en"
    assert "Accept-Language" in en.headers["vary"]
    assert _catalog_from(en.text) == load_catalog("admin", "en")
    assert _catalog_from(pt.text)["nav"]["models"] == "Modelos"


def test_admin_cookie_overrides_browser_and_selector_is_rendered():
    c = TestClient(app, cookies={"iic_locale": "pt-BR"})
    r = c.get("/admin", headers={"Accept-Language": "en"})
    assert '<html lang="pt-BR">' in r.text
    assert 'id="lang-select"' in r.text and '<option value="pt-BR" selected>' in r.text


def test_admin_pseudo_locale_marks_every_catalog_text():
    r = client.get("/admin/usage?lng=xx")
    assert "⟦Real token consumption (open period)⟧" in r.text
    assert '<html lang="en">' in r.text


# --- erros com code ----------------------------------------------------------


def test_admin_api_errors_carry_code(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "admin-test-key-73")
    bad = client.get("/admin/api/models", headers={"X-API-Admin-Key": "errada"})
    assert bad.status_code == 401 and bad.json()["code"] == "invalid_key"
    nodb = client.get("/admin/api/models", headers={"X-API-Admin-Key": "admin-test-key-73"})
    assert nodb.status_code == 503
    assert nodb.json()["code"] == "db_not_configured"
    assert nodb.json()["detail"]  # detail continua para clientes antigos


def test_every_admin_error_code_has_text_in_both_locales():
    src = (ROOT / "app/admin/routes.py").read_text() + (ROOT / "app/auth_guard.py").read_text()
    codes = set(re.findall(r'api_error\(\s*\d+,\s*"(\w+)"', src))
    assert codes, "nenhum api_error encontrado"
    for loc in ("pt-BR", "en"):
        errors = load_catalog("admin", loc)["errors"]
        assert codes <= set(errors), (loc, sorted(codes - set(errors)))


def test_gate_fails_without_frontend_i18n(tmp_path):
    from app.evaluation.gates import check_ui_i18n

    findings = check_ui_i18n(tmp_path)
    assert findings[0].is_failure and "frontend/src/i18n" in findings[0].message
