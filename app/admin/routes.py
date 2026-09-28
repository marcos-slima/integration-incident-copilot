"""Rotas /admin/api/* — CRUD do registro de modelos + metering (DA-46/47/48).

Autenticacao: ADMIN_API_KEY dedicada (header X-API-Admin-Key,
app/admin/security.py) em TODAS as rotas deste router. A separacao
`/admin/api/*` (JSON, protegido) de `/admin/*` (paginas Jinja2 finas,
sem dado sensivel — os dados so chegam via API com a chave) e intencional:
a UI admin e a shell; o detalhe do que existe no banco exige auth.

Sem DATABASE_URL → HTTP 503 "Persistencia nao configurada" (fail-closed:
nenhuma rota admin finge sucesso sem banco por tras).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.repository import AdminRepository
from app.admin.security import verify_admin_key
from app.db import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/api", dependencies=[Depends(verify_admin_key)])

SessionDep = Annotated[AsyncSession | None, Depends(get_db_session)]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ModelCreate(BaseModel):
    provider_origin: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=128)
    base_url: str | None = None
    price_in_per_1m: float | None = Field(default=None, ge=0)
    price_out_per_1m: float | None = Field(default=None, ge=0)
    monthly_limit_tokens: int | None = Field(default=None, ge=1)
    enabled: bool = True
    is_default: bool = False
    notes: str | None = None


class ModelPatch(BaseModel):
    base_url: str | None = None
    price_in_per_1m: float | None = Field(default=None, ge=0)
    price_out_per_1m: float | None = Field(default=None, ge=0)
    monthly_limit_tokens: int | None = Field(default=None, ge=1)
    enabled: bool | None = None
    is_default: bool | None = None
    notes: str | None = None


class CredentialIn(BaseModel):
    key: str = Field(
        min_length=1, description="Chave de API do provedor (grava cifrada, nunca retorna)"
    )


class SystemCreate(BaseModel):
    system_key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=128)
    vendor: str = Field(min_length=1, max_length=64)
    connector_type: str = Field(min_length=1, max_length=32)
    base_url: str | None = None
    environment: str = Field(default="prod", min_length=1, max_length=16)
    status: str = Field(default="active", min_length=1, max_length=16)
    notes: str | None = None


class SystemPatch(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    vendor: str | None = Field(default=None, max_length=64)
    connector_type: str | None = Field(default=None, max_length=32)
    base_url: str | None = None
    environment: str | None = Field(default=None, max_length=16)
    status: str | None = Field(default=None, max_length=16)
    notes: str | None = None


def _require_session(session: SessionDep) -> AsyncSession:
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Persistencia nao configurada",
        )
    return session


SessionReq = Annotated[AsyncSession, Depends(_require_session)]


def _model_out(model: Any, usage: Any | None) -> dict[str, Any]:
    from app.admin.models import percent_consumed

    tokens_in = usage.tokens_in if usage else 0
    tokens_out = usage.tokens_out if usage else 0
    return {
        "id": str(model.id),
        "provider_origin": model.provider_origin,
        "model_id": model.model_id,
        "base_url": model.base_url,
        "price_in_per_1m": model.price_in_per_1m,
        "price_out_per_1m": model.price_out_per_1m,
        "monthly_limit_tokens": model.monthly_limit_tokens,
        "enabled": model.enabled,
        "is_default": model.is_default,
        "notes": model.notes,
        "created_at": model.created_at.isoformat() if model.created_at else None,
        "usage": {
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
        },
    }


# ---------------------------------------------------------------------------
# Status do registro
# ---------------------------------------------------------------------------


@router.get("/registry/status")
async def registry_status(
    session: SessionDep,
) -> dict[str, Any]:
    """Estado do registro: flags de config e, se banco vivo, contagem."""
    from app.admin.crypto import master_key_configured
    from app.config import settings

    result: dict[str, Any] = {
        "managed": settings.llm_registry_db,
        "db_configured": bool(settings.database_url),
        "master_key_configured": master_key_configured(),
        "metering_enabled": settings.metering_enabled,
        "models_count": None,
        "systems_count": None,
    }
    if session is not None:
        repo = AdminRepository(session)
        result["models_count"] = len(await repo.list_models())
        result["systems_count"] = len(await repo.list_systems())
    return result


# ---------------------------------------------------------------------------
# Modelos (DA-46)
# ---------------------------------------------------------------------------


@router.get("/models")
async def list_models(session: SessionReq) -> list[dict[str, Any]]:
    repo = AdminRepository(session)
    models = await repo.list_models()
    out: list[dict[str, Any]] = []
    for model in models:
        usage = await repo.get_open_usage(model.provider_origin, model.model_id)
        out.append(_model_out(model, usage))
    return out


@router.post("/models", status_code=status.HTTP_201_CREATED)
async def create_model(
    payload: ModelCreate,
    session: SessionReq,
) -> dict[str, Any]:
    repo = AdminRepository(session)
    existing = [
        m
        for m in await repo.list_models()
        if m.provider_origin == payload.provider_origin and m.model_id == payload.model_id
    ]
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Modelo '{payload.model_id}' ja registrado na origem '{payload.provider_origin}'",
        )
    model = await repo.create_model(**payload.model_dump())
    return _model_out(model, None)


@router.get("/models/{model_id}")
async def get_model(
    model_id: str,
    session: SessionReq,
) -> dict[str, Any]:
    repo = AdminRepository(session)
    model = await repo.get_model(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Modelo nao encontrado")
    usage = await repo.get_open_usage(model.provider_origin, model.model_id)
    return _model_out(model, usage)


@router.patch("/models/{model_id}")
async def patch_model(
    model_id: str,
    payload: ModelPatch,
    session: SessionReq,
) -> dict[str, Any]:
    repo = AdminRepository(session)
    model = await repo.update_model(model_id, payload.model_dump(exclude_unset=True))
    if model is None:
        raise HTTPException(status_code=404, detail="Modelo nao encontrado")
    usage = await repo.get_open_usage(model.provider_origin, model.model_id)
    return _model_out(model, usage)


@router.delete("/models/{model_id}")
async def delete_model(
    model_id: str,
    session: SessionReq,
) -> dict[str, Any]:
    repo = AdminRepository(session)
    deleted = await repo.delete_model(model_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Modelo nao encontrado")
    return {"status": "deleted", "id": model_id}


# ---------------------------------------------------------------------------
# Credenciais (DA-47)
# ---------------------------------------------------------------------------


@router.get("/credentials")
async def list_credentials(session: SessionReq) -> list[dict[str, Any]]:
    repo = AdminRepository(session)
    out: list[dict[str, Any]] = []
    for model in await repo.list_models():
        origin = model.provider_origin
        if origin in {r["provider_origin"] for r in out}:
            continue
        cred = await repo.get_credential(origin)
        out.append(
            {
                "provider_origin": origin,
                "credential_set": cred is not None,
                "masked": cred.masked if cred else None,
                "key_version": cred.key_version if cred else None,
                "last_test_status": cred.last_test_status if cred else None,
            }
        )
    return out


@router.put("/credentials/{provider_origin}")
async def set_credential(
    provider_origin: str,
    payload: CredentialIn,
    session: SessionReq,
) -> dict[str, Any]:
    from app.admin.crypto import master_key_configured

    if not master_key_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "LLM_CREDENTIALS_MASTER_KEY nao configurada — impossivel cifrar "
                "a credencial (DA-47). Configure a master key no .env."
            ),
        )
    repo = AdminRepository(session)
    cred = await repo.set_credential(provider_origin, payload.key)
    return {
        "provider_origin": cred.provider_origin,
        "masked": cred.masked,
        "key_version": cred.key_version,
    }


@router.delete("/credentials/{provider_origin}")
async def delete_credential(
    provider_origin: str,
    session: SessionReq,
) -> dict[str, Any]:
    repo = AdminRepository(session)
    deleted = await repo.delete_credential(provider_origin)
    if not deleted:
        raise HTTPException(status_code=404, detail="Credencial nao encontrada")
    return {"status": "deleted", "provider_origin": provider_origin}


# ---------------------------------------------------------------------------
# Metering (DA-48)
# ---------------------------------------------------------------------------


@router.get("/usage")
async def usage_summary(session: SessionReq) -> list[dict[str, Any]]:
    repo = AdminRepository(session)
    return await repo.usage_summary()


@router.post("/usage/{provider_origin}/{model_id}/reset")
async def reset_usage(
    provider_origin: str,
    model_id: str,
    session: SessionReq,
) -> dict[str, Any]:
    repo = AdminRepository(session)
    new_period = await repo.reset_usage_period(provider_origin, model_id)
    if new_period is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Nenhum periodo aberto de uso para '{model_id}' na origem '{provider_origin}'"
            ),
        )
    return {"status": "reset", "provider_origin": provider_origin, "model_id": model_id}


# ---------------------------------------------------------------------------
# Sistemas integrados (DA-49, Fase B)
# ---------------------------------------------------------------------------


def _system_out(system: Any) -> dict[str, Any]:
    return {
        "id": str(system.id),
        "system_key": system.system_key,
        "name": system.name,
        "vendor": system.vendor,
        "connector_type": system.connector_type,
        "base_url": system.base_url,
        "environment": system.environment,
        "status": system.status,
        "notes": system.notes,
        "created_at": system.created_at.isoformat() if system.created_at else None,
        "updated_at": system.updated_at.isoformat() if system.updated_at else None,
    }


def _validate_system_values(
    connector_type: str | None = None,
    system_environment: str | None = None,
    system_status: str | None = None,
) -> None:
    from app.admin.models import CONNECTOR_TYPES, SYSTEM_ENVIRONMENTS, SYSTEM_STATUSES

    bad: list[str] = []
    if connector_type is not None and connector_type not in CONNECTOR_TYPES:
        bad.append(
            f"connector_type invalido: {connector_type!r} (aceita: {', '.join(CONNECTOR_TYPES)})"
        )
    if system_environment is not None and system_environment not in SYSTEM_ENVIRONMENTS:
        bad.append(
            f"environment invalido: {system_environment!r} (aceita: {', '.join(SYSTEM_ENVIRONMENTS)})"
        )
    if system_status is not None and system_status not in SYSTEM_STATUSES:
        bad.append(f"status invalido: {system_status!r} (aceita: {', '.join(SYSTEM_STATUSES)})")
    if bad:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="; ".join(bad)
        )


@router.get("/systems")
async def list_systems(session: SessionReq) -> list[dict[str, Any]]:
    repo = AdminRepository(session)
    return [_system_out(s) for s in await repo.list_systems()]


@router.post("/systems", status_code=status.HTTP_201_CREATED)
async def create_system(
    payload: SystemCreate,
    session: SessionReq,
) -> dict[str, Any]:
    _validate_system_values(payload.connector_type, payload.environment, payload.status)
    repo = AdminRepository(session)
    if await repo.get_system(key=payload.system_key) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"system_key '{payload.system_key}' ja registrado",
        )
    system = await repo.create_system(**payload.model_dump())
    return _system_out(system)


@router.get("/systems/{system_id}")
async def get_system(
    system_id: str,
    session: SessionReq,
) -> dict[str, Any]:
    repo = AdminRepository(session)
    system = await repo.get_system(system_id)
    if system is None:
        raise HTTPException(status_code=404, detail="Sistema nao encontrado")
    return _system_out(system)


@router.patch("/systems/{system_id}")
async def patch_system(
    system_id: str,
    payload: SystemPatch,
    session: SessionReq,
) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    _validate_system_values(
        connector_type=updates.get("connector_type"),
        system_environment=updates.get("environment"),
        system_status=updates.get("status"),
    )
    repo = AdminRepository(session)
    system = await repo.update_system(system_id, updates)
    if system is None:
        raise HTTPException(status_code=404, detail="Sistema nao encontrado")
    return _system_out(system)


@router.delete("/systems/{system_id}")
async def delete_system(
    system_id: str,
    session: SessionReq,
) -> dict[str, Any]:
    repo = AdminRepository(session)
    deleted = await repo.delete_system(system_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Sistema nao encontrado")
    return {"status": "deleted", "id": system_id}
