"""Protecoes de autenticacao (validacao 2026-10-07, SEC-03).

Tres coisas que o rate limit por rota nao cobria:

1. **Chave invalida sem limite.** As dependencies de chave (X-API-Key,
   X-API-Admin-Key, Event Mesh) recusam ANTES do decorator do slowapi contar
   a tentativa, e o bucket padrao era o proprio header enviado - trocar o
   valor a cada tentativa zerava o contador. Aqui a falha e contada por IP
   e por superficie, numa janela fixa de 60 s; acima de
   `settings.auth_failures_per_minute` a resposta passa a ser 429.
2. **Logout sem efeito no servidor.** O cookie de sessao e assinado e
   stateless: apagar no navegador nao invalidava uma copia. Agora cada sessao
   tem um `sid`; logout grava o `sid` como revogado ate a expiracao.
3. **Usuario desativado continuava logado.** Desativar/apagar um usuario
   grava um "revogado a partir de" por usuario; sessoes emitidas antes disso
   deixam de valer.

Armazenamento: Redis quando `REDIS_URL` esta configurada (compartilhado entre
replicas); sem Redis, memoria do processo - vale so dentro do pod, mesmo
trade-off de app/events/idempotency.py.
"""

from __future__ import annotations

import logging
import threading
import time

from app.config import settings

logger = logging.getLogger(__name__)

_PREFIX = "authguard:"
_WINDOW_SECONDS = 60

_lock = threading.Lock()
_mem_counters: dict[str, tuple[int, float]] = {}  # chave -> (contagem, fim da janela)
_mem_expiring: dict[str, tuple[str, float]] = {}  # chave -> (valor, expira_em)

_redis_client = None
_redis_retry_after = 0.0


def _redis():
    """Cliente Redis ou None (sem REDIS_URL ou indisponivel; tenta de novo em 30 s)."""
    global _redis_client, _redis_retry_after
    if _redis_client is not None:
        return _redis_client
    if not settings.redis_url or time.monotonic() < _redis_retry_after:
        return None
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        client.ping()
        _redis_client = client
        return client
    except Exception as exc:  # noqa: BLE001 - qualquer falha = fallback em memoria
        logger.warning("[auth_guard] Redis indisponivel, usando memoria do processo: %s", exc)
        _redis_retry_after = time.monotonic() + 30
        return None


def reset_for_tests() -> None:
    global _redis_client
    with _lock:
        _mem_counters.clear()
        _mem_expiring.clear()
    _redis_client = None


# ---------------------------------------------------------------------------
# 1. Falhas de autenticacao por IP
# ---------------------------------------------------------------------------


def _failure_key(scope: str, ip: str) -> str:
    return f"{_PREFIX}fail:{scope}:{ip}"


def too_many_failures(scope: str, ip: str | None) -> bool:
    if not ip:
        return False
    limite = settings.auth_failures_per_minute
    chave = _failure_key(scope, ip)
    r = _redis()
    if r is not None:
        try:
            valor = r.get(chave)
            return valor is not None and int(valor) >= limite
        except Exception as exc:  # noqa: BLE001 - Redis caiu: segue em memoria
            logger.warning("[auth_guard] Redis falhou, usando memoria: %s", exc)
    with _lock:
        contagem, fim = _mem_counters.get(chave, (0, 0.0))
        return time.time() < fim and contagem >= limite


def register_failure(scope: str, ip: str | None) -> None:
    if not ip:
        return
    chave = _failure_key(scope, ip)
    r = _redis()
    if r is not None:
        try:
            pipe = r.pipeline()
            pipe.incr(chave)
            pipe.expire(chave, _WINDOW_SECONDS, nx=True)
            pipe.execute()
            return
        except Exception as exc:  # noqa: BLE001 - Redis caiu: segue em memoria
            logger.warning("[auth_guard] Redis falhou, usando memoria: %s", exc)
    agora = time.time()
    with _lock:
        contagem, fim = _mem_counters.get(chave, (0, 0.0))
        if agora >= fim:
            contagem, fim = 0, agora + _WINDOW_SECONDS
        _mem_counters[chave] = (contagem + 1, fim)


# ---------------------------------------------------------------------------
# 2/3. Revogacao de sessao
# ---------------------------------------------------------------------------


def _set_expiring(chave: str, valor: str, ttl_seconds: int) -> None:
    ttl = max(1, int(ttl_seconds))
    r = _redis()
    if r is not None:
        try:
            r.set(chave, valor, ex=ttl)
            return
        except Exception as exc:  # noqa: BLE001 - Redis caiu: segue em memoria
            logger.warning("[auth_guard] Redis falhou, usando memoria: %s", exc)
    with _lock:
        _mem_expiring[chave] = (valor, time.time() + ttl)


def _get_expiring(chave: str) -> str | None:
    r = _redis()
    if r is not None:
        try:
            valor = r.get(chave)
            return valor.decode() if isinstance(valor, bytes) else valor
        except Exception as exc:  # noqa: BLE001 - Redis caiu: segue em memoria
            logger.warning("[auth_guard] Redis falhou, usando memoria: %s", exc)
    with _lock:
        item = _mem_expiring.get(chave)
        if item is None:
            return None
        valor, expira = item
        if time.time() >= expira:
            _mem_expiring.pop(chave, None)
            return None
        return valor


def revoke_session(sid: str, ttl_seconds: int) -> None:
    """Revoga um sid ate a expiracao natural do cookie."""
    _set_expiring(f"{_PREFIX}sid:{sid}", "1", ttl_seconds)


def is_session_revoked(sid: str) -> bool:
    return _get_expiring(f"{_PREFIX}sid:{sid}") is not None


def revoke_user_sessions(username: str) -> None:
    """Invalida toda sessao do usuario emitida ate agora (desativar/apagar)."""
    _set_expiring(
        f"{_PREFIX}user:{username}",
        str(int(time.time())),
        settings.session_ttl_hours * 3600 + 60,
    )


def user_revoked_at(username: str) -> int | None:
    valor = _get_expiring(f"{_PREFIX}user:{username}")
    return int(valor) if valor else None


def check_key(
    provided: str | None, configured: str | None, *, scope: str, request, detail: str
) -> None:
    """Compara a chave com compare_digest e aplica o limite de falhas por IP.

    429 quando o IP ja errou `auth_failures_per_minute` vezes nesta janela
    (mesmo que a chave agora esteja certa - o bloqueio e da janela); 401 e
    conta a falha quando a chave nao confere."""
    import secrets as _secrets

    from fastapi import HTTPException, status

    ip = request.client.host if request is not None and request.client else None
    if too_many_failures(scope, ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="muitas tentativas de autenticacao invalidas; aguarde um minuto",
            headers={"Retry-After": str(_WINDOW_SECONDS)},
        )
    if not _secrets.compare_digest(provided or "", configured or ""):
        register_failure(scope, ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)
