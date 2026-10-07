"""SEC-03 (validacao 2026-10-07): tempo de login, revogacao de sessao,
tentativas do codigo de telefone e limite de chave invalida."""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import auth_guard
from app.admin.models import WebUser
from app.auth import (
    SESSION_COOKIE,
    hash_password,
    parse_web_users,
    sign_session,
    verify_password,
    verify_session,
)
from app.config import settings
from app.webusers import (
    confirm_email,
    confirm_phone,
    create_user,
    generate_phone_code,
    hash_phone_code,
    sign_email_token,
)

# Senha de fixture (nao e credencial real).
_SENHA_FIXTURE = "fixture-" + "senha-forte-123"


def _tempo(fn, n=3) -> float:
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t0) / n


def test_login_inexistente_custa_o_mesmo_que_existente() -> None:
    """R16: antes ~0,05 ms x ~160 ms."""
    users = parse_web_users("marcos:" + hash_password(_SENHA_FIXTURE))
    t_exist = _tempo(lambda: verify_password(users, "marcos", "errada"))
    t_ghost = _tempo(lambda: verify_password(users, "fantasma", "errada"))
    assert t_ghost > 0.5 * t_exist, (t_ghost, t_exist)


def test_logout_revoga_copia_do_cookie(monkeypatch) -> None:
    """R22: antes a copia continuava valida apos o logout."""
    from app.main import app

    monkeypatch.setattr(settings, "session_secret", "segredo-teste")
    monkeypatch.setattr(settings, "web_ui_users", "marcos:" + hash_password(_SENHA_FIXTURE))
    c = TestClient(app)
    r = c.post("/auth/login", json={"username": "marcos", "password": _SENHA_FIXTURE})
    assert r.status_code == 200
    copia = r.cookies.get(SESSION_COOKIE)
    assert verify_session(copia, settings.session_secret) == "marcos"
    c.post("/auth/logout")
    outro = TestClient(app)
    outro.cookies.set(SESSION_COOKIE, copia)
    assert outro.get("/auth/session").json() == {"authenticated": False}


def test_usuario_desativado_perde_sessoes_emitidas() -> None:
    token = sign_session("ana", 3600, "s")
    assert verify_session(token, "s") == "ana"
    auth_guard.revoke_user_sessions("ana")
    assert verify_session(token, "s") is None
    time.sleep(1.1)  # resolucao de 1 s do carimbo
    assert verify_session(sign_session("ana", 3600, "s"), "s") == "ana"


def test_token_adulterado_ou_formato_antigo_rejeitado() -> None:
    token = sign_session("ana", 3600, "s")
    _nome, *resto = token.split(":")
    assert verify_session(":".join(["admin", *resto]), "s") is None
    assert verify_session("ana:ffffffff:assinatura", "s") is None


def test_chave_invalida_vira_429_depois_do_limite(monkeypatch) -> None:
    """Antes: 70 chaves aleatorias em /diagnose, nenhum 429."""
    from app.main import app

    monkeypatch.setattr(settings, "api_key", "k" * 32)
    monkeypatch.setattr(settings, "auth_failures_per_minute", 10)
    c = TestClient(app)
    codes = [
        c.post(
            "/diagnose", json={"description": "x"}, headers={"X-API-Key": secrets.token_hex(8)}
        ).status_code
        for _ in range(12)
    ]
    assert codes[:10] == [401] * 10
    assert codes[10:] == [429, 429]


def test_chave_admin_invalida_tambem_e_limitada(monkeypatch) -> None:
    from app.main import app

    monkeypatch.setattr(settings, "admin_api_key", "a" * 32)
    monkeypatch.setattr(settings, "auth_failures_per_minute", 3)
    c = TestClient(app)
    h = {"X-API-Admin-Key": "errada"}
    codes = [c.get("/admin/api/registry/status", headers=h).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]


def test_bucket_do_rate_limit_ignora_chave_invalida(monkeypatch) -> None:
    from starlette.requests import Request

    from app.rate_limit import request_client_identity

    monkeypatch.setattr(settings, "api_key", "k" * 32)

    def req(headers):
        scope = {
            "type": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": ("10.0.0.7", 1234),
        }
        return Request(scope)

    assert request_client_identity(req({"X-API-Key": "lixo-1"})) == "10.0.0.7"
    assert request_client_identity(req({"X-API-Key": "lixo-2"})) == "10.0.0.7"
    valido = request_client_identity(req({"X-API-Key": "k" * 32}))
    assert valido.startswith("apikey:") and "k" * 32 not in valido


@pytest_asyncio.fixture
async def db(monkeypatch) -> AsyncSession:
    monkeypatch.setattr(settings, "session_secret", "segredo-de-teste")
    monkeypatch.setattr(settings, "phone_code_max_attempts", 3)
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(WebUser.__table__.create, checkfirst=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_codigo_de_telefone_invalida_apos_tentativas(db: AsyncSession) -> None:
    await create_user(
        db,
        username="pedro",
        email="pedro@corp.com",
        phone="+5511888888888",
        password="senha-inicial-456",
        created_by="test",
    )
    await confirm_email(
        db, username="pedro", token=sign_email_token("pedro", 3600, settings.session_secret)
    )
    code = generate_phone_code()
    user = await db.scalar(select(WebUser).where(WebUser.username == "pedro"))
    user.phone_code_hash = hash_phone_code(code, settings.session_secret)
    user.phone_code_expires_at = datetime.now(UTC) + timedelta(seconds=600)
    user.phone_code_attempts = 0
    await db.commit()
    errado = "000000" if code != "000000" else "111111"
    for _ in range(3):
        with pytest.raises(LookupError):
            await confirm_phone(db, username="pedro", code=errado)
    # o codigo CERTO nao vale mais: foi invalidado na 3a tentativa
    with pytest.raises(LookupError):
        await confirm_phone(db, username="pedro", code=code)
    user = await db.scalar(select(WebUser).where(WebUser.username == "pedro"))
    assert user.phone_code_hash is None and user.status == "pending_phone"
