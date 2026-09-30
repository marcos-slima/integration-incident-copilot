"""DA-54 — login de sessao para a UI web.

Cada promessa do modulo tem teste que a quebra de proposito:

- fail-closed: sem WEB_UI_USERS, /auth/login responde 401 SEMPRE
  (nao existe "login aberto", no mesmo espirito DA-18);
- o cookie de sessao autentica /diagnose (o caso do usuario da UI,
  que antes precisava da X-API-Key de infra na mao);
- X-API-Key continua valendo (maquina, DA-18 intacto);
- MCP/admin NAO aceitam cookie de sessao (superficies de maquina);
- token adulterado, expirado e segredo errado caem no 401;
- usuario inexistente e senha errada dao a MESMA resposta (nao revela
  quais logins existem);
- logout limpa o cookie.

Padrao de tests/test_api.py: client module-level (sem lifespan — o
lifespan e' de startup real, e o auth aqui e' testado com settings
monkeypatchados no objeto COMPARTILHADO de app.config, o mesmo que
app.auth le), run_diagnosis stubado (o alvo e' auth, nao o pipeline).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.auth import hash_password, parse_web_users, sign_session, verify_password, verify_session
from app.config import settings
from app.main import app
from app.models import DiagnosisResponse
from app.rate_limit import limiter

client = TestClient(app)


def _stub_diagnosis(request):
    return DiagnosisResponse(
        probable_root_cause="Causa raiz de teste (stub)",
        model_confidence=0.75,
        diagnosis_confidence=0.0,
        next_steps=["Passo 1"],
        report_markdown="## Diagnostico\n\nCausa raiz de teste (stub)",
        matched_source="doc_teste.md",
    )


@pytest.fixture
def user_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Um usuario valido + segredo de sessao fixo (deterministico).

    Patcha o objeto COMPARTILHADO de settings (app.config.settings) —
    app.auth le o mesmo objeto, entao o patch vale para a dependency.
    """
    # cookie jar limpo: o client e' MODULE-LEVEL e o httpx guarda cookies
    # entre testes — sem isso, um login de teste anterior autentica o
    # teste seguinte (achado real: /diagnose voltava 200 num teste que
    # devia provar o 401 sem nada).
    client.cookies.clear()
    # API_KEY vazia no processo de teste (conftest isola do .env): sem
    # uma chave NAO-vazia aqui, o vazio==vazio do DA-18 autentica os
    # testes que deveriam provar o 401 — o mesmo padrao dos testes de
    # auth de tests/test_api.py (chave explicita via monkeypatch).
    monkeypatch.setattr(settings, "api_key", "chave-de-maquina-do-fixture")
    monkeypatch.setattr(settings, "web_ui_users", f"marcos:{hash_password('senha-correta')}")
    monkeypatch.setattr(settings, "session_secret", "segredo-de-teste")
    monkeypatch.setattr(main_module, "run_diagnosis", _stub_diagnosis)
    limiter.reset()


@pytest.fixture
def logged_in(user_env: None) -> TestClient:
    client.post("/auth/login", json={"username": "marcos", "password": "senha-correta"})
    return client


class TestLogin:
    def test_login_certo_devolve_cookie_httponly(self, user_env: None) -> None:
        res = client.post("/auth/login", json={"username": "marcos", "password": "senha-correta"})
        assert res.status_code == 200
        # mesmo shape de /auth/session — o campo `authenticated` e o que
        # a UI usa para navegar; sem ele, login certo ainda prende o app
        # na tela de login (bug real da homologacao)
        assert res.json() == {
            "authenticated": True,
            "username": "marcos",
            "ttl_hours": settings.session_ttl_hours,
        }
        set_cookie = res.headers["set-cookie"].lower()
        assert "iic_session=" in set_cookie
        assert "httponly" in set_cookie
        assert "samesite=strict" in set_cookie
        assert "path=/" in set_cookie

    def test_session_depois_do_login_diz_quem_e(self, logged_in: TestClient) -> None:
        res = client.get("/auth/session")
        assert res.status_code == 200
        body = res.json()
        assert body["authenticated"] is True
        assert body["username"] == "marcos"
        assert body["ttl_hours"] == settings.session_ttl_hours

    def test_senha_errada_e_401(self, user_env: None) -> None:
        res = client.post("/auth/login", json={"username": "marcos", "password": "errada"})
        assert res.status_code == 401

    def test_usuario_inexistente_mesma_resposta_que_senha_errada(self, user_env: None) -> None:
        errada = client.post("/auth/login", json={"username": "marcos", "password": "x"})
        inexistente = client.post("/auth/login", json={"username": "ghost", "password": "x"})
        assert errada.status_code == inexistente.status_code == 401
        assert errada.json() == inexistente.json()

    def test_sem_usuarios_configurados_login_fica_fechado(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail-closed: WEB_UI_USERS vazio NAO abre login — nao ha
        credencial nenhuma, ninguem entra."""
        monkeypatch.setattr(settings, "web_ui_users", "")
        monkeypatch.setattr(settings, "session_secret", "segredo-de-teste")
        limiter.reset()
        res = client.post("/auth/login", json={"username": "q", "password": "q"})
        assert res.status_code == 401

    def test_logout_limpa_o_cookie(self, logged_in: TestClient) -> None:
        res = client.post("/auth/logout")
        assert res.status_code == 200
        # depois do logout, /diagnose volta a exigir auth
        res = client.post("/diagnose", json={"description": "teste"})
        assert res.status_code == 401


class TestDiagnoseComSessao:
    def test_cookie_de_sessao_autentica_diagnose(self, logged_in: TestClient) -> None:
        res = client.post(
            "/diagnose",
            json={
                "description": "IDoc travado com status 51",
                "interface_type": "rfc",
                "identifier": "RFC-IDOC-51-DEMO",
            },
        )
        assert res.status_code == 200
        assert res.json()["probable_root_cause"] == "Causa raiz de teste (stub)"

    def test_x_api_key_continua_valendo_para_maquinas(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "api_key", "chave-de-maquina")
        monkeypatch.setattr(main_module, "run_diagnosis", _stub_diagnosis)
        limiter.reset()
        res = client.post(
            "/diagnose",
            headers={"X-API-Key": "chave-de-maquina"},
            json={"description": "iFlow com 401"},
        )
        assert res.status_code == 200

    def test_sem_header_e_sem_cookie_e_401(self, user_env: None) -> None:
        res = client.post("/diagnose", json={"description": "teste"})
        assert res.status_code == 401


class TestTokenDeSessao:
    def test_token_adulterado_e_rejeitado(self, user_env: None) -> None:
        token = sign_session("marcos", 3600, settings.session_secret)
        client.cookies.set("iic_session", token[:-4] + "beef")
        res = client.post("/diagnose", json={"description": "teste"})
        assert res.status_code == 401

    def test_token_expirado_e_rejeitado(self, user_env: None) -> None:
        token = sign_session("marcos", -10, settings.session_secret)
        assert verify_session(token, settings.session_secret) is None
        client.cookies.set("iic_session", token)
        res = client.post("/diagnose", json={"description": "teste"})
        assert res.status_code == 401

    def test_segredo_de_assinatura_errado_e_rejeitado(self) -> None:
        token = sign_session("marcos", 3600, "segredo-certo")
        assert verify_session(token, "segredo-errado") is None


class TestSuperficiesDeMaquina:
    def test_mcp_nao_aceita_cookie_de_sessao(
        self, logged_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """O middleware do MCP compara X-API-Key contra settings.api_key —
        chave NAO-vazia garante que cookie sozinho nao passa por
        empty==empty (fail-open de teste, nao do app real: o startup
        real garante chave via _ensure_api_keys_configured)."""
        monkeypatch.setattr(settings, "api_key", "chave-de-maquina")
        res = client.post("/mcp", headers={"Accept": "application/json, text/event-stream"})
        assert res.status_code == 401

    def test_admin_nao_aceita_cookie_de_sessao(
        self, logged_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "admin_api_key", "chave-admin")
        res = client.get("/admin/api/registry/status")
        assert res.status_code == 401


class TestParseWebUsers:
    def test_entrada_malformada_e_pulada_sem_abrir_auth(self) -> None:
        users = parse_web_users("marcos:sem-dois-pontos,outro:pbkdf2_sha256.zzz.zz.zz")
        assert users == {}

    def test_hash_password_gera_formato_que_o_parse_aceita(self) -> None:
        assert list(parse_web_users(f"marcos:{hash_password('x')}")) == ["marcos"]

    def test_iteracoes_fracas_sao_rejeitadas(self) -> None:
        """Piso de 100k: PBKDF2 com 50k e' rapido demais de brute-force."""
        entrada = f"marcos:{hash_password('x', iterations=50_000)}"
        assert parse_web_users(entrada) == {}

    def test_senha_certa_e_errada(self) -> None:
        users = parse_web_users(f"marcos:{hash_password('correta')}")
        assert verify_password(users, "marcos", "correta") is True
        assert verify_password(users, "marcos", "errada") is False
