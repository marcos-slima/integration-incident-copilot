"""DA-54 — login de sessao para a UI web (POST /auth/login).

O problema: DA-18 exige X-API-Key em /diagnose — credencial de BORDA,
pensada para maquina-a-maquina (curl, MCP, A2A, outro agente). Mas a UI
web reusava essa chave de infra, jogando um segredo do servidor na mao
do usuario final, que precisaria abrir o .env do servidor para copiar
o valor. O comentario de frontend/src/api/diagnose.ts ja previa esta
evolucao: "autenticacao via cookie de sessao obtido atraves de um
endpoint /auth/login dedicado".

A solucao: duas vias de auth em /diagnose, cada uma certa para a sua
camada —

  - maquina: header X-API-Key, exatamente como antes (DA-18 intacto);
  - humano: POST /auth/login (usuario+senha) → cookie de sessao
    HttpOnly, SameSite=Strict, assinado com HMAC-SHA256.

Superficies so-de-maquina (MCP /mcp, A2A /a2a, Event Mesh, /admin) NAO
aceitam cookie de sessao: continuam exigindo as suas chaves dedicadas
(DA-18/DA-27/DA-46). Um cookie de browser nao abre nenhum delas.

Fail-closed, no espirito DA-18: sem WEB_UI_USERS configurado, /auth/login
responde 401 SEMPRE (nao ha "modo aberto"); SESSION_SECRET vazio gera um
segredo efemero no startup com WARNING no log. Todas as comparacoes de
segredo usam secrets.compare_digest; senha e verificada com PBKDF2-SHA256
(hashlib, stdlib — nenhuma dependencia nova).

Formato de WEB_UI_USERS (uma ou mais entradas, separadas por virgula):

    WEB_UI_USERS="marcos:pbkdf2_sha256.600000.<salt_hex>.<hash_hex>"

O separador e PONTO, nao `$`, por causa do docker compose: valores com `$`
passam pela interpolacao de ${VAR:-} no compose.yaml e o pedaco entre `$`
vira variavel-inexistente — o salt SUMIA do WEB_UI_USERS no container
(achado real na homologacao: login sempre 401, env truncado no meio).

Gerar o hash de uma senha (o mesmo esquema do Fernet em app/admin/crypto.py,
DA-47 — one-liner documentada em vez de ferramenta propria):

    uv run python -c "from app.auth import hash_password; print(hash_password('sua senha'))"
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from app.config import settings
from app.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_COOKIE = "iic_session"
PBKDF2_ITERATIONS = 600_000  # OWASP 2023 para PBKDF2-SHA256
HASH_SCHEME = "pbkdf2_sha256"

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


# ---------------------------------------------------------------------------
# Senha: parse e verificacao PBKDF2 (stdlib)
# ---------------------------------------------------------------------------


class WebUser:
    """Um usuario de UI: salt + hash PBKDF2-SHA256 + iteracoes."""

    __slots__ = ("hash_hex", "iterations", "salt")

    def __init__(self, salt: bytes, iterations: int, hash_hex: str) -> None:
        self.salt = salt
        self.iterations = iterations
        self.hash_hex = hash_hex


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Gera a string `pbkdf2_sha256$iter$salt_hex$hash_hex` para o .env."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"{HASH_SCHEME}.{iterations}.{salt.hex()}.{digest.hex()}"


def parse_web_users(raw: str) -> dict[str, WebUser]:
    """`user:hash,user:hash` → dict. Entrada malformada e' PULADA com
    WARNING (nao derruba o startup), mas nunca vira auth aberta: o
    usuario so entra se a entrada dele estiver bem formada."""
    users: dict[str, WebUser] = {}
    for entrada in raw.split(","):
        entrada = entrada.strip()
        if not entrada:
            continue
        nome, sep, cred = entrada.partition(":")
        if not sep:
            logger.warning("WEB_UI_USERS: entrada sem ':' — pulada: %r", entrada[:30])
            continue
        partes = cred.strip().split(".")
        if len(partes) != 4 or partes[0] != HASH_SCHEME:
            logger.warning(
                "WEB_UI_USERS: entrada do usuario %r fora do formato "
                "pbkdf2_sha256$iter$salt$hash — pulada",
                nome,
            )
            continue
        try:
            iterations = int(partes[1])
            salt = bytes.fromhex(partes[2])
            expected = partes[3]
            if iterations < 100_000 or len(salt) < 8:
                logger.warning(
                    "WEB_UI_USERS: entrada do usuario %r com sal/iteracoes "
                    "fracos — pulada (use hash_password)",
                    nome,
                )
                continue
        except ValueError:
            logger.warning("WEB_UI_USERS: entrada do usuario %r ilegivel — pulada", nome)
            continue
        users[nome.strip()] = WebUser(salt, iterations, expected)
    return users


def verify_password(users: dict[str, WebUser], username: str, password: str) -> bool:
    """PBKDF2 recomputa o hash e compara com compare_digest. Sem timing
    que vaze tamanho/prefixo. Usuario inexistente cai no mesmo 401 —
    nao revela quais logins existem."""
    user = users.get(username)
    if user is None:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), user.salt, user.iterations)
    return secrets.compare_digest(digest.hex(), user.hash_hex)


# ---------------------------------------------------------------------------
# Sessao: cookie assinado HMAC-SHA256, stateless
# ---------------------------------------------------------------------------


def ensure_session_secret_configured() -> None:
    """Chamado no startup (app/main.py). SESSION_SECRET vazia = segredo
    efemero aleatorio por processo (WARNING no log), mesmo padrao de
    _ensure_api_keys_configured (DA-18): nunca deixa o cookie sem
    assinatura, e o aviso diz como estabilizar."""
    if settings.session_secret:
        return
    settings.session_secret = secrets.token_urlsafe(32)
    logger.warning(
        "SESSION_SECRET nao configurada no .env - gerada automaticamente "
        "para esta execucao (cookies de sessao da UI expiram a cada "
        "restart): %s",
        settings.session_secret,
    )


def sign_session(username: str, ttl_seconds: int, secret: str) -> str:
    """`usuario:expiry_hex:assinatura_hex`. A assinatura cobre usuario E
    expiry — mexer em qualquer um invalida o cookie."""
    expiry = int(time.time() + ttl_seconds)
    payload = f"{username}:{expiry:x}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_session(token: str | None, secret: str) -> str | None:
    """Valida forma, assinatura (compare_digest) e expiry. Retorna o
    username, ou None para qualquer coisa errada — o chamador decide 401."""
    if not token or not secret:
        return None
    nome, sep1, resto = token.partition(":")
    expiry_hex, sep2, sig = resto.partition(":")
    if not sep1 or not sep2:
        return None
    payload = f"{nome}:{expiry_hex}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(sig, expected):
        return None
    try:
        expiry = int(expiry_hex, 16)
    except ValueError:
        return None
    if time.time() > expiry:
        return None
    return nome


def session_cookie_kwargs() -> dict[str, object]:
    max_age = settings.session_ttl_hours * 3600
    return {
        "key": SESSION_COOKIE,
        "max_age": max_age,
        "httponly": True,
        "samesite": "strict",
        "secure": settings.session_cookie_secure,
        "path": "/",
    }


# ---------------------------------------------------------------------------
# Request/response e rotas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def _users() -> dict[str, WebUser]:
    return parse_web_users(settings.web_ui_users)


@router.post("/login")
@limiter.limit("5/minute")
def login(request: Request, body: LoginRequest, response: Response) -> dict[str, str | bool]:
    """Troca usuario+senha por cookie de sessao HttpOnly.

    Fail-closed: sem WEB_UI_USERS configurado, responde 401 sempre —
    nao existe "login aberto". Rate limit 5/min por IP — mais apertado
    que o 10/min de /diagnose, porque aqui se testa senha.
    """
    users = _users()
    if not users or not verify_password(users, body.username, body.password):
        # Mesma resposta para usuario inexistente e senha errada.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="usuario ou senha invalidos",
        )
    ttl = settings.session_ttl_hours * 3600
    token = sign_session(body.username, ttl, settings.session_secret)
    response.set_cookie(value=token, **session_cookie_kwargs())
    logger.info("login de UI aceito (usuario=%s, ip=%s)", body.username, request.client.host)
    return {"ok": True, "username": body.username}


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    """Limpa o cookie. Idempotente: sair sem sessao tambem e' 200."""
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/session")
def whoami(request: Request) -> dict[str, str | bool | int]:
    """Estado da sessao — e' o que a UI consulta para saber se pede login."""
    username = verify_session(request.cookies.get(SESSION_COOKIE), settings.session_secret)
    if username is None:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "username": username,
        # a UI mostra o prazo da sessao sem hardcode — vem da mesma fonte
        # que assina o cookie (settings.session_ttl_hours)
        "ttl_hours": settings.session_ttl_hours,
    }


# ---------------------------------------------------------------------------
# Helper para a dependency de /diagnose (composta em app/main.py)
# ---------------------------------------------------------------------------


def verify_session_cookie(request: Request) -> str | None:
    """Valida o cookie de sessao (DA-54). Retorna o username ou None.

    A dependency COMPLETA (X-API-Key OU sessao) vive em app/main.py, junto
    de verify_api_key: a parte da chave le o settings DESTE modulo — o
    mesmo objeto que os testes monkeypatcham — e a parte da sessao le
    o settings daqui (cookie so existe se o operador configurou).
    MCP/A2A/Event Mesh/admin NAO usam nenhuma das duas: chaves dedicadas.
    """
    token = request.cookies.get(SESSION_COOKIE)
    return verify_session(token, settings.session_secret)
