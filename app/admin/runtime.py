"""DA-46 — resolucao de runtime a partir do registro (LLM_REGISTRY_DB).

Quando o operador liga `llm_registry_db=true`, get_chat_model
(app/llm/factory.py) para de ler SO o .env e pergunta ao registro qual
modelo/base_url/credencial usar para a ORIGEM em uso (DA-45). Este
modulo e a ponte javascript→banco: leitura SYNC best-effort (psycopg2,
mesmo padrao do escritor de metering/incidents), chamada LAZY dentro de
get_chat_model — o import deste modulo em runtime do gateway nao entra
no boot.

Fail-closed (invariante do plano de manutencao):
  - registro nao acessivel/banco off            → None → ConfigurationError
  - origem sem modelo habilitado                → None → ConfigurationError
  - origem cloud sem credencial no registro     → None → ConfigurationError
  - NUNCA cai silenciosamente de volta para o .env quando o modo
    registry esta ligado.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.admin import crypto
from app.admin.models import LlmCredential, LlmModel
from app.config import settings
from app.llm.origins import resolve_provider_origin

logger = logging.getLogger(__name__)

# Providers cuja "origem local" nao exige chave de API no registro
# (o Ollama conversa por token-free na rede). Tudo o mais exige
# credencial cifrada (DA-47), sob pena de fail-closed.
_LOCAL_PROVIDERS = {"ollama"}


def resolve_runtime_model(provider: str, model_name: str, cfg: Any) -> dict[str, Any] | None:
    """Resolve {model_id, base_url, api_key} para o provider atual.

    Retorna None (falho-fechado) em qualquer condicao que nao permita
    um destino explicito — querer diagnosticar, nao adivinhar."""
    origin = resolve_provider_origin(provider, cfg)
    if not origin:
        return None
    if not settings.database_url:
        return None

    try:
        from app.admin.repository import _get_sync_session_factory

        with _get_sync_session_factory()() as session:
            row = session.execute(
                select(LlmModel).where(
                    LlmModel.provider_origin == origin,
                    LlmModel.model_id == model_name,
                    LlmModel.enabled.is_(True),
                )
            ).scalar_one_or_none()
            if row is None:
                # fallback: o default habilitado da origem
                row = session.execute(
                    select(LlmModel).where(
                        LlmModel.provider_origin == origin,
                        LlmModel.enabled.is_(True),
                        LlmModel.is_default.is_(True),
                    )
                ).scalar_one_or_none()
            if row is None:
                return None

            if provider in _LOCAL_PROVIDERS:
                api_key = None
            else:
                cred = session.execute(
                    select(LlmCredential).where(LlmCredential.provider_origin == origin)
                ).scalar_one_or_none()
                if cred is None:
                    return None
                api_key = crypto.decrypt_secret(cred.encrypted_key)

            return {
                "model_id": row.model_id,
                "base_url": row.base_url,
                "api_key": api_key,
                "monthly_limit_tokens": row.monthly_limit_tokens,
                "price_in_per_1m": row.price_in_per_1m,
                "price_out_per_1m": row.price_out_per_1m,
            }
    except Exception:
        logger.exception(
            "[registry/DA-46] Falha ao resolver modelo do registro "
            "(origin=%s model=%s) — fail-closed.",
            origin,
            model_name,
        )
        return None
