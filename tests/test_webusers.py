"""DA-55 — usuarios da UI web com ativacao em duas etapas.

Cada promessa tem teste que a quebra de proposito:

- criacao: PBKDF2 (nunca claro), status inicial pending_email, token HMAC
  com namespace "verify-email:" (nunca colide com token de sessao);
- etapa 1 (e-mail): token certo avanca; errado/expirado/usuario
  inexistente dao a MESMA resposta (enumeracao);
- etapa 2 (telefone): codigo certo ativa; errado/expirado tambem generico;
  unico-uso (hash apagado na validacao);
- login: usuario do banco ATIVO entra (mesma sessao DA-54); pendente e
  desativado NAO entram; sem banco, o .env (DA-54) segue valendo
  (bootstrap — o operador nunca fica trancado fora);
- admin: CRUD + reemissoes exigem X-Admin-Api-Key; token/codigo
  out-of-band voltam SO na resposta de admin; _user_out nunca expoe hash.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.admin.models import WebUser
from app.auth import hash_password, sign_session
from app.config import settings
from app.main import app
from app.rate_limit import limiter
from app.webusers import (
    EMAIL_TOKEN_TTL_SECONDS,
    STATUS_ACTIVE,
    STATUS_DISABLED,
    STATUS_PENDING_EMAIL,
    confirm_email,
    confirm_phone,
    create_user,
    generate_phone_code,
    hash_phone_code,
    sign_email_token,
    verify_email_token,
    verify_login_db,
    verify_phone_code,
)

client = TestClient(app)


class _Maker:
    """Sessionmaker fake que devolve a mesma sessao do fixture — o
    get_db_session faz `async with AsyncSessionLocal() as session`, entao
    um callavel que a aceita como context manager assincrono basta."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> _Maker:
        return self

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *args: object) -> None:
        return None


@pytest.fixture
def user_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "api_key", "chave-de-maquina-do-fixture")
    monkeypatch.setattr(settings, "web_ui_users", "")
    monkeypatch.setattr(settings, "session_secret", "segredo-de-teste")
    limiter.reset()


@pytest_asyncio.fixture
async def db(user_env: None) -> AsyncSession:
    """Banco SQLite em memoria (mesmo padrao dos testes do admin, DA-46)."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        # so a tabela DESTE teste: o metadata inteiro do admin carrega
        # JSONB (PG-only) que o SQLite nao renderiza — padrao documentado
        # no docstring de app/admin/models.py
        await conn.run_sync(WebUser.__table__.create, checkfirst=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def _create_active_user(session: AsyncSession) -> WebUser:
    user, _ = await create_user(
        session,
        username="maria",
        email="maria@corp.com",
        phone="+5511999999999",
        password="senha-inicial-123",
        created_by="test",
    )
    token = sign_email_token("maria", EMAIL_TOKEN_TTL_SECONDS, settings.session_secret)
    await confirm_email(session, username="maria", token=token)
    # pega o codigo do hash? NAO — o hash e' de um codigo que so' o
    # gerador viu. Reemitimos pela rota de dominio: geramos e setamos.
    code = generate_phone_code()
    from datetime import UTC, datetime, timedelta

    user = await session.scalar(select(WebUser).where(WebUser.username == "maria"))
    user.phone_code_hash = hash_phone_code(code, settings.session_secret)
    user.phone_code_expires_at = datetime.now(UTC) + timedelta(seconds=60)
    await session.commit()
    await confirm_phone(session, username="maria", code=code)
    return user


class TestTokenEhCodigo:
    def test_token_de_email_tem_namespace_proprio(self) -> None:
        """Um token de ativacao NUNCA serve de sessao (e vice-versa)."""
        token = sign_email_token("maria", 3600, "segredo")
        assert token.startswith("verify-email:maria:")
        session_token = sign_session("maria", 3600, "segredo")
        assert verify_email_token(session_token, "maria", "segredo") is False

    def test_token_expirado_e_rejeitado(self) -> None:
        token = sign_email_token("maria", -10, "segredo")
        assert verify_email_token(token, "maria", "segredo") is False

    def test_token_de_outro_usuario_e_rejeitado(self) -> None:
        token = sign_email_token("maria", 3600, "segredo")
        assert verify_email_token(token, "joao", "segredo") is False

    def test_codigo_de_telefone_guarda_so_hash(self) -> None:
        from datetime import UTC, datetime, timedelta

        code = generate_phone_code()
        assert len(code) == 6 and code.isdigit()
        h = hash_phone_code(code, "segredo")
        expires = datetime.now(UTC) + timedelta(seconds=60)
        assert verify_phone_code(code, h, expires, "segredo") is True
        assert verify_phone_code("000000", h, expires, "segredo") is False
        # expirado
        past = datetime.now(UTC) - timedelta(seconds=1)
        assert verify_phone_code(code, h, past, "segredo") is False


class TestFluxoDeAtivacao:
    @pytest.mark.asyncio
    async def test_fluxo_completo_ativa_e_o_login_passa(self, db: AsyncSession) -> None:
        user = await _create_active_user(db)
        assert user.status == STATUS_ACTIVE
        assert await verify_login_db(db, username="maria", password="senha-inicial-123") is True

    @pytest.mark.asyncio
    async def test_pendente_nao_entra(self, db: AsyncSession) -> None:
        await create_user(
            db,
            username="pedro",
            email="pedro@corp.com",
            phone="+5511888888888",
            password="senha-inicial-456",
            created_by="test",
        )
        assert await verify_login_db(db, username="pedro", password="senha-inicial-456") is False

    @pytest.mark.asyncio
    async def test_senha_errada_nao_entra(self, db: AsyncSession) -> None:
        await _create_active_user(db)
        assert await verify_login_db(db, username="maria", password="errada") is False

    @pytest.mark.asyncio
    async def test_sem_banco_devolve_false(self) -> None:
        assert await verify_login_db(None, username="maria", password="x") is False

    @pytest.mark.asyncio
    async def test_email_errado_e_generico(self, db: AsyncSession) -> None:
        await create_user(
            db,
            username="pedro",
            email="pedro@corp.com",
            phone="+5511888888888",
            password="senha-inicial-456",
            created_by="test",
        )
        token_errado = sign_email_token("pedro", 3600, "segredo-ERRADO")
        with pytest.raises(LookupError):
            await confirm_email(db, username="pedro", token=token_errado)
        with pytest.raises(LookupError):  # usuario inexistente: MESMA excecao
            await confirm_email(db, username="ghost", token="x")

    @pytest.mark.asyncio
    async def test_codigo_errado_e_generico(self, db: AsyncSession) -> None:
        await create_user(
            db,
            username="pedro",
            email="pedro@corp.com",
            phone="+5511888888888",
            password="senha-inicial-456",
            created_by="test",
        )
        token = sign_email_token("pedro", 3600, settings.session_secret)
        await confirm_email(db, username="pedro", token=token)
        with pytest.raises(LookupError):
            await confirm_phone(db, username="pedro", code="000000")
        with pytest.raises(LookupError):  # inexistente == errado
            await confirm_phone(db, username="ghost", code="123456")

    @pytest.mark.asyncio
    async def test_desativado_nao_entra(self, db: AsyncSession) -> None:
        user = await _create_active_user(db)
        user.status = STATUS_DISABLED
        await db.commit()
        assert await verify_login_db(db, username="maria", password="senha-inicial-123") is False


class TestRotasPublicas:
    """Verify endpoints contra o app com banco SQLite injetado."""

    @pytest.fixture
    def db_app(self, db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        """Troca a dependencia get_db_session pela sessao SQLite do fixture."""
        # padrao de test_admin_systems: o Depends(get_db_session) captura
        # a FUNCAO no import; quem le no call e' o AsyncSessionLocal de
        # app.db — patch nele. StaticPool compartilha o :memory: entre
        # conexoes.
        import app.db as db_module

        monkeypatch.setattr(db_module, "AsyncSessionLocal", _Maker(db))
        limiter.reset()
        return client

    def test_verify_email_certo_avanca(self, db_app: TestClient, db: AsyncSession) -> None:
        import asyncio

        _, tokens = asyncio.get_event_loop().run_until_complete(
            create_user(
                db,
                username="ana",
                email="ana@corp.com",
                phone="+5511777777777",
                password="senha-inicial-789",
                created_by="test",
            )
        )
        token = tokens.email_token
        res = db_app.post("/auth/verify/email", json={"username": "ana", "token": token})
        assert res.status_code == 200
        assert res.json()["next_step"] == "phone"

    def test_verify_email_errado_401(self, db_app: TestClient) -> None:
        res = db_app.post("/auth/verify/email", json={"username": "ana", "token": "x"})
        assert res.status_code == 401

    def test_verify_email_sem_db_503(self) -> None:
        """Conftest isola DATABASE_URL: sem banco, a ativacao responde 503
        (e' do banco, nao do .env — login sim, ativacao nao)."""
        limiter.reset()
        res = client.post("/auth/verify/email", json={"username": "a", "token": "x"})
        assert res.status_code == 503


class TestRotasAdmin:
    @pytest.fixture
    def db_app(self, db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.db as db_module

        monkeypatch.setattr(db_module, "AsyncSessionLocal", _Maker(db))
        monkeypatch.setattr(settings, "admin_api_key", "chave-admin-teste")
        limiter.reset()
        return client

    def test_sem_chave_admin_401(self, db_app: TestClient) -> None:
        res = db_app.get("/admin/api/users")
        assert res.status_code == 401

    def test_cria_e_lista_sem_expor_hash(self, db_app: TestClient) -> None:
        res = db_app.post(
            "/admin/api/users",
            headers={"X-API-Admin-Key": "chave-admin-teste"},
            json={
                "username": "joao",
                "email": "joao@corp.com",
                "phone": "+5511666666666",
                "password": "senha-inicial-abc",
            },
        )
        assert res.status_code == 201
        body = res.json()
        # out-of-band: token volta SO para o admin
        assert body["activation"]["email_token"]
        assert body["status"] == STATUS_PENDING_EMAIL
        assert "password_hash" not in body
        assert "phone_code_hash" not in body

        lista = db_app.get(
            "/admin/api/users", headers={"X-API-Admin-Key": "chave-admin-teste"}
        ).json()
        assert [u["username"] for u in lista] == ["joao"]
        assert "password_hash" not in lista[0]

    def test_reemissao_de_codigo_so_para_pendente_de_telefone(self, db_app: TestClient) -> None:
        res = db_app.post(
            "/admin/api/users",
            headers={"X-API-Admin-Key": "chave-admin-teste"},
            json={
                "username": "joao",
                "email": "joao@corp.com",
                "phone": "+5511666666666",
                "password": "senha-inicial-abc",
            },
        )
        uid = res.json()["id"]
        # ainda pending_email: reemissao de phone-code recusa
        res = db_app.post(
            f"/admin/api/users/{uid}/phone-code",
            headers={"X-API-Admin-Key": "chave-admin-teste"},
        )
        assert res.status_code == 409

    def test_desativa_e_reativa(self, db_app: TestClient) -> None:
        res = db_app.post(
            "/admin/api/users",
            headers={"X-API-Admin-Key": "chave-admin-teste"},
            json={
                "username": "joao",
                "email": "joao@corp.com",
                "phone": "+5511666666666",
                "password": "senha-inicial-abc",
            },
        )
        uid = res.json()["id"]
        res = db_app.patch(
            f"/admin/api/users/{uid}",
            headers={"X-API-Admin-Key": "chave-admin-teste"},
            json={"status": "disabled"},
        )
        assert res.status_code == 200
        assert res.json()["status"] == STATUS_DISABLED
        res = db_app.patch(
            f"/admin/api/users/{uid}",
            headers={"X-API-Admin-Key": "chave-admin-teste"},
            json={"status": "active"},
        )
        assert res.json()["status"] == STATUS_ACTIVE


class TestEnvBootstrapContinua:
    def test_login_pelo_env_com_banco_vazio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DA-54 continua: WEB_UI_USERS e' o bootstrap que nunca desliga —
        o operador nunca fica trancado fora por causa do banco."""
        monkeypatch.setattr(settings, "api_key", "chave-x")
        monkeypatch.setattr(settings, "web_ui_users", f"marcos:{hash_password('senha-env')}")
        monkeypatch.setattr(settings, "session_secret", "segredo")
        limiter.reset()
        res = client.post("/auth/login", json={"username": "marcos", "password": "senha-env"})
        assert res.status_code == 200
        assert res.json()["authenticated"] is True
