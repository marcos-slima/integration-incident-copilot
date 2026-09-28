"""DA-46/48 — acesso ao registro de modelos, credenciais e metering.

Duas camadas com responsabilidades distintas:

  AdminRepository (async, por request — get_db_session)
      CRUD usado pelas rotas /admin/* (modelo/catalogo + credencial +
      summary de uso). Instanciado por request, igual IncidentRepository.

  record_usage (sync, best-effort, por status no runtime)
      Escrito pelo metering (app/admin/metering.py) dentro do gateway
      (app/llm/gateway.py::invoke_via_gateway, que e sincrono). Mesmo
      padrao de app/services/incident_recorder.py: sem DATABASE_URL ou
      com erro de banco, registra e NUNCA quebra o diagnostico.

  resolve_runtime_model (sync, best-effort) — ver app/admin/runtime.py:
      usado pelo factory (get_chat_model) quando LLM_REGISTRY_DB=true.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import crypto
from app.admin.models import (
    IntegrationSystem,
    LlmCredential,
    LlmModel,
    LlmUsage,
    percent_consumed,
)
from app.config import settings

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Async: rotas /admin (per request)
# ---------------------------------------------------------------------------


class AdminRepository:
    """CRUD do registro de modelos + credenciais + metering (async)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- modelos -----------------------------------------------------------

    async def list_models(self, include_disabled: bool = True) -> list[LlmModel]:
        stmt = select(LlmModel).order_by(LlmModel.provider_origin, LlmModel.model_id)
        result = await self._session.execute(stmt)
        models = list(result.scalars())
        if include_disabled:
            return models
        return [m for m in models if m.enabled]

    async def get_model(self, model_id: uuid.UUID | str) -> LlmModel | None:
        if isinstance(model_id, str):
            try:
                model_id = uuid.UUID(model_id)
            except ValueError:
                return None
        result = await self._session.execute(select(LlmModel).where(LlmModel.id == model_id))
        return result.scalar_one_or_none()

    async def create_model(
        self,
        *,
        provider_origin: str,
        model_id: str,
        base_url: str | None = None,
        price_in_per_1m: float | None = None,
        price_out_per_1m: float | None = None,
        monthly_limit_tokens: int | None = None,
        enabled: bool = True,
        is_default: bool = False,
        notes: str | None = None,
    ) -> LlmModel:
        row = LlmModel(
            provider_origin=provider_origin.strip(),
            model_id=model_id.strip(),
            base_url=(base_url or "").strip() or None,
            price_in_per_1m=price_in_per_1m,
            price_out_per_1m=price_out_per_1m,
            monthly_limit_tokens=monthly_limit_tokens,
            enabled=enabled,
            is_default=is_default,
            notes=notes or None,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def update_model(
        self, model_id: uuid.UUID | str, fields: dict[str, Any]
    ) -> LlmModel | None:
        model = await self.get_model(model_id)
        if model is None:
            return None
        allow = {
            "base_url",
            "price_in_per_1m",
            "price_out_per_1m",
            "monthly_limit_tokens",
            "enabled",
            "is_default",
            "notes",
        }
        for key, value in fields.items():
            if key not in allow:
                continue
            if key == "base_url":
                value = (value or "").strip() or None
            setattr(model, key, value)
        model.updated_at = _now()
        await self._session.flush()
        return model

    async def delete_model(self, model_id: uuid.UUID | str) -> bool:
        model = await self.get_model(model_id)
        if model is None:
            return False
        await self._session.delete(model)
        await self._session.flush()
        return True

    # -- credenciais (DA-47) ------------------------------------------------

    async def set_credential(self, provider_origin: str, plaintext_key: str) -> LlmCredential:
        """Grava (ou rotaciona) a credencial cifrada de uma origem.

        `plaintext_key` existe apenas aqui, em memoria, na request — o
        banco guarda o token Fernet e a mascara. Nunca retornamos o
        plaintext para o chamador."""
        origin = provider_origin.strip()
        encrypted = crypto.encrypt_secret(plaintext_key)
        masked = crypto.mask_secret(plaintext_key)
        existing = await self.get_credential(origin)
        if existing is not None:
            existing.encrypted_key = encrypted
            existing.masked = masked
            existing.key_version += 1
            existing.last_test_status = None
            existing.last_tested_at = None
            existing.updated_at = _now()
            await self._session.flush()
            return existing
        row = LlmCredential(
            provider_origin=origin,
            encrypted_key=encrypted,
            key_version=1,
            masked=masked,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_credential(self, provider_origin: str) -> LlmCredential | None:
        result = await self._session.execute(
            select(LlmCredential).where(LlmCredential.provider_origin == provider_origin.strip())
        )
        return result.scalar_one_or_none()

    async def delete_credential(self, provider_origin: str) -> bool:
        row = await self.get_credential(provider_origin)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def decrypt_credential(self, provider_origin: str) -> str | None:
        """Plaintext da credencial de uma origem (uso exclusivo do
        runtime, DA-46 — factory managed mode). None se nao existir."""
        row = await self.get_credential(provider_origin)
        if row is None:
            return None
        return crypto.decrypt_secret(row.encrypted_key)

    # -- metering (DA-48) ---------------------------------------------------

    async def get_open_usage(self, provider_origin: str, model_id: str) -> LlmUsage | None:
        result = await self._session.execute(
            select(LlmUsage).where(
                LlmUsage.provider_origin == provider_origin,
                LlmUsage.model_id == model_id,
                LlmUsage.period_end.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def reset_usage_period(self, provider_origin: str, model_id: str) -> LlmUsage | None:
        """Fecha o periodo aberto (period_end=agora) e abre um novo vazio."""
        open_row = await self.get_open_usage(provider_origin, model_id)
        if open_row is None:
            return None
        open_row.period_end = _now()
        await self._session.flush()
        new_row = LlmUsage(provider_origin=provider_origin, model_id=model_id)
        self._session.add(new_row)
        await self._session.flush()
        return new_row

    async def usage_summary(self, include_disabled: bool = True) -> list[dict[str, Any]]:
        """Linhas de metering enriquecidas com modelo, precos e percentual.

        percent: percent_consumed(tokens, limite) — null sem limite."""
        models = await self.list_models(include_disabled=include_disabled)
        rows: list[dict[str, Any]] = []
        for model in models:
            usage = await self.get_open_usage(model.provider_origin, model.model_id)
            tokens_in = usage.tokens_in if usage else 0
            tokens_out = usage.tokens_out if usage else 0
            rows.append(
                {
                    "provider_origin": model.provider_origin,
                    "model_id": model.model_id,
                    "enabled": model.enabled,
                    "is_default": model.is_default,
                    "base_url": model.base_url,
                    "price_in_per_1m": model.price_in_per_1m,
                    "price_out_per_1m": model.price_out_per_1m,
                    "monthly_limit_tokens": model.monthly_limit_tokens,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "tokens_total": tokens_in + tokens_out,
                    "requests": usage.requests if usage else 0,
                    "failures": usage.failures if usage else 0,
                    "cost_usd": round(usage.cost_usd, 4) if usage else 0.0,
                    "percent": percent_consumed(
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        monthly_limit_tokens=model.monthly_limit_tokens,
                    ),
                    "period_start": usage.period_start.isoformat() if usage else None,
                }
            )
        return rows

    # -- sistemas integrados (DA-49, Fase B) -------------------------------

    async def list_systems(self) -> list[IntegrationSystem]:
        stmt = select(IntegrationSystem).order_by(
            IntegrationSystem.vendor, IntegrationSystem.system_key
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def get_system(
        self, system_id: uuid.UUID | str | None = None, *, key: str | None = None
    ) -> IntegrationSystem | None:
        """Busca por id OU por system_key. Sem argumento: None."""
        if system_id is not None:
            if isinstance(system_id, str):
                try:
                    system_id = uuid.UUID(system_id)
                except ValueError:
                    system_id = None
            if system_id is not None:
                result = await self._session.execute(
                    select(IntegrationSystem).where(IntegrationSystem.id == system_id)
                )
                return result.scalar_one_or_none()
        if key is not None:
            result = await self._session.execute(
                select(IntegrationSystem).where(IntegrationSystem.system_key == key.strip())
            )
            return result.scalar_one_or_none()
        return None

    async def create_system(
        self,
        *,
        system_key: str,
        name: str,
        vendor: str,
        connector_type: str,
        base_url: str | None = None,
        environment: str = "prod",
        status: str = "active",
        notes: str | None = None,
    ) -> IntegrationSystem:
        row = IntegrationSystem(
            system_key=system_key.strip(),
            name=name.strip(),
            vendor=vendor.strip(),
            connector_type=connector_type.strip(),
            base_url=(base_url or "").strip() or None,
            environment=environment.strip(),
            status=status.strip(),
            notes=notes or None,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def update_system(
        self, system_id: uuid.UUID | str, fields: dict[str, Any]
    ) -> IntegrationSystem | None:
        system = await self.get_system(system_id)
        if system is None:
            return None
        allow = {"name", "vendor", "connector_type", "base_url", "environment", "status", "notes"}
        for key, value in fields.items():
            if key not in allow:
                continue
            if key == "base_url":
                value = (value or "").strip() or None
            setattr(system, key, value)
        system.updated_at = _now()
        await self._session.flush()
        return system

    async def delete_system(self, system_id: uuid.UUID | str) -> bool:
        system = await self.get_system(system_id)
        if system is None:
            return False
        await self._session.delete(system)
        await self._session.flush()
        return True


# ---------------------------------------------------------------------------
# Sync best-effort: escritor do metering (via status no runtime, DA-48)
# ---------------------------------------------------------------------------


def _sync_url(raw: str) -> str:
    for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
        if raw.startswith(prefix):
            return "postgresql+psycopg2://" + raw[len(prefix) :]
    return raw


@lru_cache(maxsize=1)
def _get_sync_session_factory():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        _sync_url(settings.database_url),
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=3,
        connect_args={"connect_timeout": 3},
    )
    return sessionmaker(engine, expire_on_commit=False)


def record_usage(
    origin: str,
    model_id: str,
    *,
    tokens_in: int = 0,
    tokens_out: int = 0,
    requests: int = 1,
    failures: int = 0,
    cost_usd: float = 0.0,
) -> bool:
    """Acumula uso no periodo aberto de (origin, model_id) — best-effort.

    Retorna False (sem excecao) quando DATABASE_URL nao esta configurada
    ou o banco falha: o metering observa, o diagnostico e o produto
    (mesma filosofia de record_incident). Cria o periodo aberto na
    primeira chamada da tupla."""
    if not settings.database_url or not settings.metering_enabled:
        return False
    try:
        from sqlalchemy import select

        with _get_sync_session_factory()() as session:
            from app.admin.models import LlmUsage

            existing = session.execute(
                select(LlmUsage).where(
                    LlmUsage.provider_origin == origin,
                    LlmUsage.model_id == model_id,
                    LlmUsage.period_end.is_(None),
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    LlmUsage(
                        provider_origin=origin,
                        model_id=model_id,
                        tokens_in=int(tokens_in or 0),
                        tokens_out=int(tokens_out or 0),
                        requests=int(requests or 0),
                        failures=int(failures or 0),
                        cost_usd=float(cost_usd or 0.0),
                    )
                )
            else:
                existing.tokens_in += int(tokens_in or 0)
                existing.tokens_out += int(tokens_out or 0)
                existing.requests += int(requests or 0)
                existing.failures += int(failures or 0)
                existing.cost_usd += float(cost_usd or 0.0)
                existing.updated_at = _now()
            session.commit()
        return True
    except Exception:
        logger.exception(
            "[metering] Falha ao gravar uso em llm_usage (%s/%s) — diagnostico nao afetado",
            origin,
            model_id,
        )
        return False
