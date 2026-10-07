"""DA-55 — dominio de usuarios da UI web (ativacao em duas etapas).

Fluxo (ativacao UNICA, decidida com o dono — logins seguintes sao
usuario+senha, sessao DA-54):

    admin cria usuario (username/email/telefone/senha inicial)
        -> status=pending_email, token HMAC por e-mail (24h)
    usuario confirma o token (/auth/verify/email)
        -> status=pending_phone, codigo de 6 digitos por telefone (10 min)
    usuario confirma o codigo (/auth/verify/phone)
        -> status=active — login vale dali em diante

Entrega OUT-OF-BAND na homologacao (decidida com o dono): sem SMTP/SMS
configurados, o token/codigo NAO e' publicado em nenhum lugar publico —
volta para o ADMIN na resposta da API de admin (superficie X-API-Admin-Key)
com WARNING no log. Adaptador de entrega real (SMTP/provedor SMS) e' o
ponto de extensao: `deliver_email`/`deliver_sms` — quando houver
credencial, o modo out-of-band desliga sozinho.

Seguranca, no padrao da casa (DA-18/54):

- senha: PBKDF2-SHA256, MESMO formato da DA-54 (ponto como separador);
- token de e-mail: HMAC-SHA256 com namespace "verify-email:" — NUNCA
  colide com token de sessao;
- codigo de telefone: 6 digitos, guardado so' como HASH (sha256 com o
  segredo de sessao), unico-uso, 10 min;
- enumeracao: usuario inexistente e token errado dao a MESMA resposta;
- rate limit nas rotas publicas: 5/min (e' onde se testa segredo).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.models import WebUser
from app.config import settings

logger = logging.getLogger(__name__)

STATUS_PENDING_EMAIL = "pending_email"
STATUS_PENDING_PHONE = "pending_phone"
STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"

EMAIL_TOKEN_TTL_SECONDS = 24 * 3600
PHONE_CODE_TTL_SECONDS = 10 * 60


# ---------------------------------------------------------------------------
# Entrega (adaptador) — out-of-band agora, real depois
# ---------------------------------------------------------------------------


@dataclass
class DeliveryResult:
    delivered: bool  # True = canal real enviou; False = out-of-band
    channel: str  # "email" | "sms"


def deliver_email(to: str, body: str) -> DeliveryResult:
    """Envia o token por e-mail quando EMAIL_PROVIDER estiver configurado.

    Validacao 2026-10-07 (M-12): `app/notifications/` (Mailpit e Resend)
    existia mas nunca era chamado - este adaptador so logava "out-of-band".
    Agora usa o sender do provedor configurado; sem provedor, ou se o envio
    falhar, continua out-of-band: o token volta SO para o admin
    (X-API-Admin-Key), nunca para uma rota publica. `delivered=True` so
    quando o provedor confirmou o envio - e e isso que faz o token sair da
    resposta da API admin.
    """
    from app.notifications.providers import get_email_sender

    sender = get_email_sender()
    if sender is not None:
        result = sender.send(to, "Ativacao de acesso - Integration Incident Copilot", body)
        if result.success:
            logger.info("DA-55: token de e-mail enviado para %s via %s", to, result.provider)
            return DeliveryResult(delivered=True, channel="email")
        logger.warning(
            "DA-55: envio via %s falhou (%s) - token segue out-of-band pela API admin",
            result.provider,
            result.error_message,
        )
        return DeliveryResult(delivered=False, channel="email")
    logger.warning(
        "DA-55 out-of-band: token de e-mail para %s NAO foi enviado — EMAIL_PROVIDER "
        "nao configurado. O token segue na resposta da API de admin (canal "
        "X-API-Admin-Key).",
        to,
    )
    return DeliveryResult(delivered=False, channel="email")


def deliver_sms(to: str, body: str) -> DeliveryResult:
    """Envia o codigo por SMS/WhatsApp — QUANDO houver provedor.

    Mesmo contrato de deliver_email: out-of-band ate' haver credencial de
    provedor (Twilio/AWS SNS/etc). O codigo volta so' para o admin.
    """
    logger.warning(
        "DA-55 out-of-band: codigo de telefone para %s NAO foi enviado — "
        "provedor de SMS nao configurado. O codigo segue na resposta da API "
        "de admin (canal X-API-Admin-Key).",
        to,
    )
    return DeliveryResult(delivered=False, channel="sms")


# ---------------------------------------------------------------------------
# Token de e-mail (HMAC, namespace proprio)
# ---------------------------------------------------------------------------


def sign_email_token(username: str, ttl_seconds: int, secret: str) -> str:
    """`verify-email:username:expiry:assinatura` — namespace distinto do
    token de sessao (DA-54): um token de ativacao nunca serve de sessao
    e vice-versa, mesmo com o mesmo segredo."""
    payload = f"verify-email:{username}:{int(time.time() + ttl_seconds):x}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_email_token(token: str, username: str, secret: str) -> bool:
    """Valida forma, namespace, assinatura (compare_digest) e expiry."""
    if not token or not secret:
        return False
    prefixo = f"verify-email:{username}:"
    if not token.startswith(prefixo):
        return False
    resto = token[len(prefixo) :]
    expiry_hex, sep, sig = resto.partition(":")
    if not sep:
        return False
    payload = f"{prefixo}{expiry_hex}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(sig, expected):
        return False
    try:
        expiry = int(expiry_hex, 16)
    except ValueError:
        return False
    return time.time() <= expiry


# ---------------------------------------------------------------------------
# Codigo de telefone (6 digitos, so' o hash guardado)
# ---------------------------------------------------------------------------


def generate_phone_code() -> str:
    """6 digitos, `secrets` (CSPRNG), sem '000000' trivial de adivinhar."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_phone_code(code: str, secret: str) -> str:
    """sha256(secret + codigo) — o banco nunca guarda o codigo em si."""
    return hashlib.sha256(f"{secret}:{code}".encode()).hexdigest()


def verify_phone_code(
    code: str, code_hash: str | None, expires_at: datetime | None, secret: str
) -> bool:
    """compare_digest + expiry. Unico-uso e' garantido pela transicao de
    status (quem chama apaga o hash quando valida)."""
    if not code_hash or expires_at is None:
        return False
    # SQLite devolve datetime NAIVE (sem tzinfo); Postgres, aware. Sem
    # normalizar, aware-vs-naive explode em comparacao — achado pelo
    # teste com SQLite, e valido para os dois dialetos.
    expires = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
    if datetime.now(UTC) > expires:
        return False
    return secrets.compare_digest(hash_phone_code(code, secret), code_hash)


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Operacoes de dominio (sessao async do admin, padrao DA-46/49)
# ---------------------------------------------------------------------------


@dataclass
class ActivationTokens:
    """O que o admin recebe de volta ao criar/reemitir. Os segredos (token,
    codigo) so' viajam na resposta de admin quando a entrega e'
    out-of-band — entregue de verdade, vazio."""

    email_token: str | None
    phone_code: str | None
    email_delivered: bool
    phone_delivered: bool


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    email: str,
    phone: str,
    password: str,
    created_by: str,
) -> tuple[WebUser, ActivationTokens]:
    """Cria usuario pendente de e-mail + emite o token da etapa 1."""
    # import lazy: app.auth importa ESTE modulo no load (login cai aqui);
    # importar app.auth no topo criaria ciclo
    from app.auth import hash_password

    existing = await session.scalar(select(WebUser).where(WebUser.username == username))
    if existing is not None:
        raise ValueError(f"usuario ja existe: {username}")
    user = WebUser(
        username=username,
        password_hash=hash_password(password),
        email=email,
        phone=phone,
        status=STATUS_PENDING_EMAIL,
        created_by=created_by,
    )
    session.add(user)
    await session.flush()
    token = sign_email_token(username, EMAIL_TOKEN_TTL_SECONDS, settings.session_secret)
    result = deliver_email(email, f"Token de ativacao: {token}")
    await session.commit()
    tokens = ActivationTokens(
        email_token=token if not result.delivered else None,
        phone_code=None,
        email_delivered=result.delivered,
        phone_delivered=False,
    )
    return user, tokens


async def confirm_email(session: AsyncSession, *, username: str, token: str) -> ActivationTokens:
    """Etapa 1: token valido -> pending_phone + codigo de telefone emitido.

    Erro generico para usuario inexistente E token errado (enumeracao).
    """
    user = await session.scalar(select(WebUser).where(WebUser.username == username))
    if (
        user is None
        or user.status != STATUS_PENDING_EMAIL
        or not verify_email_token(token, username, settings.session_secret)
    ):
        # mesma resposta para tudo que nao fecha
        raise LookupError("token invalido ou usuario inexistente")
    user.status = STATUS_PENDING_PHONE
    user.email_verified_at = _now()
    code = generate_phone_code()
    user.phone_code_hash = hash_phone_code(code, settings.session_secret)
    user.phone_code_expires_at = _now() + timedelta(seconds=PHONE_CODE_TTL_SECONDS)
    user.phone_code_attempts = 0
    result = deliver_sms(user.phone, f"Codigo de ativacao: {code}")
    await session.commit()
    return ActivationTokens(
        email_token=None,
        phone_code=code if not result.delivered else None,
        email_delivered=True,
        phone_delivered=result.delivered,
    )


async def confirm_phone(session: AsyncSession, *, username: str, code: str) -> WebUser:
    """Etapa 2: codigo valido -> active. Hash apagado (unico-uso)."""
    user = await session.scalar(select(WebUser).where(WebUser.username == username))
    if user is None or user.status != STATUS_PENDING_PHONE or user.phone_code_hash is None:
        raise LookupError("codigo invalido ou usuario inexistente")
    if not verify_phone_code(
        code, user.phone_code_hash, user.phone_code_expires_at, settings.session_secret
    ):
        # SEC-03: o codigo vigente aceita no maximo phone_code_max_attempts
        # erros; depois e invalidado e so o admin reemite. Mesma resposta
        # generica (nao revela que o limite foi atingido).
        user.phone_code_attempts = (user.phone_code_attempts or 0) + 1
        if user.phone_code_attempts >= settings.phone_code_max_attempts:
            user.phone_code_hash = None
            user.phone_code_expires_at = None
            logger.warning(
                "codigo de telefone invalidado apos %d tentativas (usuario=%s)",
                user.phone_code_attempts,
                username,
            )
        await session.commit()
        raise LookupError("codigo invalido ou usuario inexistente")
    user.status = STATUS_ACTIVE
    user.phone_verified_at = _now()
    user.phone_code_hash = None  # unico-uso: apagado na validacao
    user.phone_code_expires_at = None
    await session.commit()
    return user


async def verify_login_db(session: AsyncSession | None, *, username: str, password: str) -> bool:
    """Login contra o BANCO (usuario ativo). `session is None` (sem
    DATABASE_URL) = False — quem decide o fallback para o .env e' o
    chamador (app/auth.py), que nunca fica trancado fora."""
    if session is None:
        return False
    user = await session.scalar(select(WebUser).where(WebUser.username == username))
    if user is None or user.status != STATUS_ACTIVE:
        # mesmo custo do caminho com usuario (SEC-03): o tempo nao pode
        # dizer se o usuario existe ou esta inativo
        from app.auth import burn_password_check

        burn_password_check(password)
        return False
    return _verify_hash(user.password_hash, username, password)


def _verify_hash(password_hash: str, username: str, password: str) -> bool:
    """Verifica PBKDF2 no formato DA-54 contra um hash guardado."""
    # imports lazy: mesmo motivo do create_user (ciclo com app.auth)
    from app.auth import parse_web_users, verify_password

    users = parse_web_users(f"{username}:{password_hash}")
    return verify_password(users, username, password)
