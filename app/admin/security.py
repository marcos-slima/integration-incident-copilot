"""DA-46/47/48 — autenticacao da superficie admin.

ADMIN_API_KEY e uma chave DEDICADA (header X-API-Admin-Key): isola o
blast radius de um vazamento da superficie admin do canal de diagnostico
(X-API-Key, DA-18), do A2A (X-A2A-Api-Key) e do Event Mesh
(X-Event-Mesh-Api-Key). A mesma semantica de `_ensure_api_keys_configured`
(DA-18) vale aqui: ADMIN_API_KEY vazia no .env NAO desabilita a auth —
`ensure_admin_key_configured` gera uma chave efemera no startup (warning
no log), preservando o "clone e rode" sem deixar /admin aberto.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config import settings

logger = logging.getLogger(__name__)

admin_key_header = APIKeyHeader(name="X-API-Admin-Key", auto_error=False)


def verify_admin_key(admin_key: str | None = Security(admin_key_header)) -> None:
    """Dependency das rotas /admin/*. Comparacao com compare_digest
    (mesmo padrao de verify_api_key, DA-18): nao vaza tamanho/prefixo."""
    if not secrets.compare_digest(admin_key or "", settings.admin_api_key or ""):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Admin-Key invalida ou ausente",
        )


def ensure_admin_key_configured() -> None:
    """Chamado no startup (app/main.py). ADMIN_API_KEY vazia = chave
    efemera aleatoria por processo (avisada ALTO no log). NUNCA deixa a
    auth admin desabilitada.

    A chave efemera muda a cada restart; para uma chave estavel,
    configure ADMIN_API_KEY no .env (ver .env.example)."""
    if settings.admin_api_key:
        return
    settings.admin_api_key = secrets.token_urlsafe(32)
    logger.warning(
        "ADMIN_API_KEY nao configurada no .env - gerada automaticamente para "
        "esta execucao (header X-API-Admin-Key, superficie /admin): %s",
        settings.admin_api_key,
    )
