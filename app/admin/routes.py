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

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.correlation import build_system_index, correlate
from app.admin.crypto import decrypt_evidence
from app.admin.models import WebUser
from app.admin.repository import AdminRepository
from app.admin.security import verify_admin_key
from app.config import settings
from app.db import get_db_session
from app.services.incident_repository import IncidentRepository

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


class WebSearchSourceCreate(BaseModel):
    interface_type: str = Field(min_length=1, max_length=32)
    site_filter: str = Field(min_length=1, max_length=2000)
    tech_term: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    notes: str | None = None


class WebSearchSourcePatch(BaseModel):
    site_filter: str | None = Field(default=None, max_length=2000)
    tech_term: str | None = Field(default=None, max_length=128)
    enabled: bool | None = None
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
        "incidents_count": None,
        "unverified_count": None,
    }
    if session is not None:
        repo = AdminRepository(session)
        result["models_count"] = len(await repo.list_models())
        result["systems_count"] = len(await repo.list_systems())
        # DA-50: contagem de incidentes e de quantos ainda nao tem veredito
        # humano - best-effort: banco sem a tabela `incidents` (migration 001
        # nao aplicada) nao pode derrubar o status do registro.
        try:
            incidents = IncidentRepository(session)
            result["incidents_count"] = await incidents.count()
            result["unverified_count"] = await incidents.count(verified=False)
        except Exception:
            logger.warning("[admin] Falha ao contar incidentes para registry/status", exc_info=True)
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


# ---------------------------------------------------------------------------
# web_search_sources (DA-57)
# ---------------------------------------------------------------------------


def _source_out(s) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "interface_type": s.interface_type,
        "site_filter": s.site_filter,
        "tech_term": s.tech_term,
        "enabled": bool(s.enabled),
        "notes": s.notes,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def _validate_source_interface_type(interface_type: str) -> None:
    """`interface_type` tem de estar no Literal do pipeline.

    Sem isso, a tabela aceitaria um conector que o grafo nunca produz e a
    linha ficaria como configuracao morta — o mesmo modo de falha que o
    gate `connector_reachable` existe para barrar nas outras superficies.
    """
    from app.admin.models import CONNECTOR_TYPES

    value = (interface_type or "").strip()
    if value not in CONNECTOR_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(f"interface_type invalido: {value!r} (aceita: {', '.join(CONNECTOR_TYPES)})"),
        )


@router.get("/web-search-sources")
async def list_web_search_sources(session: SessionReq) -> list[dict[str, Any]]:
    repo = AdminRepository(session)
    return [_source_out(s) for s in await repo.list_web_search_sources()]


@router.post("/web-search-sources", status_code=status.HTTP_201_CREATED)
async def create_web_search_source(
    payload: WebSearchSourceCreate,
    session: SessionReq,
) -> dict[str, Any]:
    _validate_source_interface_type(payload.interface_type)
    repo = AdminRepository(session)
    if await repo.get_web_search_source(interface_type=payload.interface_type) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"interface_type '{payload.interface_type}' ja tem fonte cadastrada",
        )
    try:
        source = await repo.create_web_search_source(**payload.model_dump())
        await session.commit()
    except IntegrityError:
        # O pre-check acima tem janela: dois admins criando o mesmo
        # interface_type ao mesmo tempo passam os dois. Quem decide e' a
        # constraint UNIQUE — sem este catch a segunda viraria 500.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"interface_type '{payload.interface_type}' ja tem fonte cadastrada",
        ) from None
    return _source_out(source)


@router.get("/web-search-sources/{source_id}")
async def get_web_search_source(source_id: str, session: SessionReq) -> dict[str, Any]:
    repo = AdminRepository(session)
    source = await repo.get_web_search_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Fonte de busca nao encontrada")
    return _source_out(source)


@router.patch("/web-search-sources/{source_id}")
async def patch_web_search_source(
    source_id: str,
    payload: WebSearchSourcePatch,
    session: SessionReq,
) -> dict[str, Any]:
    # interface_type e' IMUTAVEL: e' a chave que o grafo usa para casar o
    # incidente com a fonte. Trocar o valor em vez de criar outra linha
    # deixaria o cadastro mentindo sobre qual conector tem qual filtro.
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        if key in {"site_filter", "tech_term"} and not (value or "").strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{key} nao pode ficar vazio: sem ele a linha e' ignorada "
                "pela resolucao fail-closed",
            )
    repo = AdminRepository(session)
    source = await repo.update_web_search_source(source_id, updates)
    if source is None:
        raise HTTPException(status_code=404, detail="Fonte de busca nao encontrada")
    await session.commit()
    return _source_out(source)


@router.delete("/web-search-sources/{source_id}")
async def delete_web_search_source(source_id: str, session: SessionReq) -> dict[str, Any]:
    repo = AdminRepository(session)
    if not await repo.delete_web_search_source(source_id):
        raise HTTPException(status_code=404, detail="Fonte de busca nao encontrada")
    await session.commit()
    return {"status": "deleted", "id": source_id}


@router.get("/systems/{system_id}/incidents")
async def list_system_incidents(
    system_id: str,
    session: SessionReq,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Incidentes correlacionados a um sistema do catalogo (DA-50).

    Reusa EXATAMENTE a mesma regra de app/admin/correlation.py (match por
    system_key, depois por connector_type unico) que a listagem geral, para
    que o drill-down por sistema e a visao geral nunca contem numeros
    diferentes. Por isso busca em duas frentes e filtra pela correlacao
    resolvida, em vez de um unico WHERE estrito (que perderia os incidentes
    anteriores a DA-50, cujo connector_source_system era o rotulo).

    O indice e montado com o catalogo INTEIRO, nunca com `[system]`: com um
    unico sistema no indice o fallback por connector_type sempre acharia
    exatamente 1 candidato e resolveria qualquer incidente do mesmo tipo -
    o drill-down de cap_prod engoliria os incidentes de cap_stage.
    """
    admin_repo = AdminRepository(session)
    system = await admin_repo.get_system(system_id)
    if system is None:
        raise HTTPException(status_code=404, detail="Sistema nao encontrado")

    index = build_system_index(await admin_repo.list_systems())
    incidents_repo = IncidentRepository(session)
    by_source = await incidents_repo.list_recent(limit, source_system=system.system_key)
    by_interface = await incidents_repo.list_recent(limit, interface_type=system.connector_type)
    merged = {str(i.id): i for i in [*by_source, *by_interface]}
    rows: list[dict[str, Any]] = []
    for incident in merged.values():
        correlation = correlate(incident.interface_type, incident.connector_source_system, index)
        if correlation["system_key"] == system.system_key:
            rows.append(_incident_out(incident, correlation))
    rows.sort(key=lambda r: r["created_at"] or "", reverse=True)
    return rows[:limit]


# ---------------------------------------------------------------------------
# Incidentes (DA-50, Fase C) — listagem correlacionada com o catalogo
# ---------------------------------------------------------------------------


def _incident_out(
    incident: Any, correlation: dict[str, Any], *, detail: bool = False
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(incident.id),
        "created_at": incident.created_at.isoformat() if incident.created_at else None,
        "interface_type": incident.interface_type,
        "connector_source_system": incident.connector_source_system,
        "probable_root_cause": incident.probable_root_cause,
        "model_confidence": incident.model_confidence,
        "diagnosis_confidence": incident.diagnosis_confidence,
        "evidence_strength": incident.evidence_strength,
        "llm_provider_used": incident.llm_provider_used,
        "agent_domain": incident.agent_domain,
        "latency_ms": incident.latency_ms,
        "is_mock": incident.is_mock,
        "sensitivity_level": incident.sensitivity_level,
        "pii_detected": incident.pii_detected,
        "redaction_applied": incident.redaction_applied,
        "error_codes": incident.error_codes,
        "trace_id": incident.trace_id,
        "verified_at": incident.verified_at.isoformat() if incident.verified_at else None,
        "diagnosis_correct": incident.diagnosis_correct,
        "verified_by": incident.verified_by,
        "verified_root_cause": incident.verified_root_cause,
        "system": correlation,
    }
    if detail:
        out["description"] = incident.description
        out["evidence"] = decrypt_evidence(incident.evidence_json) if incident.evidence_json else []
    return out


@router.get("/incidents")
async def list_incidents(
    session: SessionReq,
    limit: int = Query(default=50, ge=1, le=200),
    interface_type: str | None = None,
    system_key: str | None = None,
    verified: bool | None = None,
) -> list[dict[str, Any]]:
    """Incidentes recentes, cada um com o sistema integrado correlacionado.

    - `system_key`: filtra pelo match EXATO (connector_source_system igual ao
      system_key). Para o drill-down com a regra completa (inclui fallback
      por connector_type), use /systems/{id}/incidents.
    - `verified=false`: so incidentes sem veredito humano.
    """
    index = build_system_index(await AdminRepository(session).list_systems())
    incidents = await IncidentRepository(session).list_recent(
        limit,
        interface_type=interface_type,
        source_system=system_key,
        verified=verified,
    )
    return [
        _incident_out(i, correlate(i.interface_type, i.connector_source_system, index))
        for i in incidents
    ]


@router.get("/incidents/{incident_id}")
async def get_incident(incident_id: str, session: SessionReq) -> dict[str, Any]:
    index = build_system_index(await AdminRepository(session).list_systems())
    incident = await IncidentRepository(session).get_by_id(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incidente nao encontrado")
    return _incident_out(
        incident,
        correlate(incident.interface_type, incident.connector_source_system, index),
        detail=True,
    )


# ---------------------------------------------------------------------------
# DA-55: usuarios da UI web (CRUD + emissoes de ativacao)
# ---------------------------------------------------------------------------


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9_.-]+$")
    email: str = Field(min_length=3, max_length=256, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str = Field(min_length=8, max_length=32, pattern=r"^\+?[0-9\s-]{8,32}$")
    password: str = Field(min_length=10, max_length=256)


class UserPatch(BaseModel):
    email: str | None = Field(default=None, max_length=256, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str | None = Field(default=None, max_length=32, pattern=r"^\+?[0-9\s-]{8,32}$")
    status: str | None = Field(default=None, max_length=16)


def _user_out(user: Any) -> dict[str, Any]:
    """NUNCA expoe password_hash nem phone_code_hash — nem em list, nem em
    detail. O hash e' o segredo; a resposta de admin e' lida por humano e
    logada em lugar que humano le."""
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "phone": user.phone,
        "status": user.status,
        "email_verified_at": user.email_verified_at.isoformat() if user.email_verified_at else None,
        "phone_verified_at": user.phone_verified_at.isoformat() if user.phone_verified_at else None,
        "created_by": user.created_by,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }


async def _get_user_or_404(session: AsyncSession, user_id: str) -> Any:
    from uuid import UUID

    try:
        uid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado") from None
    user = await session.get(WebUser, uid)
    if user is None:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado")
    return user


@router.get("/users")
async def list_users(session: SessionReq) -> list[dict[str, Any]]:
    from sqlalchemy import select

    users = (await session.scalars(select(WebUser).order_by(WebUser.created_at))).all()
    return [_user_out(u) for u in users]


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreate, session: SessionReq) -> dict[str, Any]:
    """Cria usuario PENDENTE de e-mail. A senha inicial entra como PBKDF2
    (mesmo formato DA-54) — nunca em claro em lugar nenhum.

    Out-of-band (SMTP nao configurado): o token de ativacao volta AQUI,
    na resposta de admin (canal X-API-Admin-Key), com WARNING no log.
    Entrega real: `deliver_email` em app/webusers.py — token sai da
    resposta sozinho quando o canal entrega de verdade."""
    from app.webusers import create_user as domain_create_user

    try:
        user, tokens = await domain_create_user(
            session,
            username=payload.username,
            email=payload.email,
            phone=payload.phone,
            password=payload.password,
            created_by="admin-api",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    out = _user_out(user)
    out["activation"] = {
        "email_token": tokens.email_token,  # None quando entregue de verdade
        "email_delivered": tokens.email_delivered,
        "next_step": "email",
    }
    return out


@router.post("/users/{user_id}/email-token")
async def reissue_email_token(user_id: str, session: SessionReq) -> dict[str, Any]:
    """Reemite o token da etapa 1 (so' para pendentes de e-mail)."""
    from app.webusers import EMAIL_TOKEN_TTL_SECONDS, deliver_email, sign_email_token

    user = await _get_user_or_404(session, user_id)
    if user.status != "pending_email":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"reemissao de token de e-mail exige status pending_email (atual: {user.status})",
        )
    token = sign_email_token(user.username, EMAIL_TOKEN_TTL_SECONDS, settings.session_secret)
    result = deliver_email(user.email, f"Token de ativacao: {token}")
    await session.commit()
    return {
        "id": str(user.id),
        "username": user.username,
        "activation": {
            "email_token": token if not result.delivered else None,
            "email_delivered": result.delivered,
            "next_step": "email",
        },
    }


@router.post("/users/{user_id}/phone-code")
async def reissue_phone_code(user_id: str, session: SessionReq) -> dict[str, Any]:
    """Reemite o codigo da etapa 2 (so' para pendentes de telefone).

    Out-of-band: o codigo volta AQUI (admin), nunca em rota publica."""
    from datetime import UTC, datetime, timedelta

    from app.webusers import (
        PHONE_CODE_TTL_SECONDS,
        deliver_sms,
        generate_phone_code,
        hash_phone_code,
    )

    user = await _get_user_or_404(session, user_id)
    if user.status != "pending_phone":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"reemissao de codigo exige status pending_phone (atual: {user.status})",
        )
    code = generate_phone_code()
    user.phone_code_hash = hash_phone_code(code, settings.session_secret)
    user.phone_code_expires_at = datetime.now(UTC) + timedelta(seconds=PHONE_CODE_TTL_SECONDS)
    user.phone_code_attempts = 0
    result = deliver_sms(user.phone, f"Codigo de ativacao: {code}")
    await session.commit()
    return {
        "id": str(user.id),
        "username": user.username,
        "activation": {
            "phone_code": code if not result.delivered else None,
            "phone_delivered": result.delivered,
            "next_step": "phone",
        },
    }


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: str,
    payload: UserPatch,
    session: SessionReq,
) -> dict[str, Any]:
    """Atualiza e-mail/telefone e transiciona status (active<->disabled).
    Mudanca de e-mail/telefone de usuario ATIVO reexige ativacao? Nao —
    reexige reemissao manual das etapas; documentado na DA-55."""
    user = await _get_user_or_404(session, user_id)
    if payload.status is not None:
        if payload.status not in ("active", "disabled"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="status aceita: active, disabled",
            )
        if user.status.startswith("pending") and payload.status != "disabled":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="usuario pendente so pode ser desativado ou ter as etapas reemitidas",
            )
        user.status = payload.status
        if payload.status == "disabled":
            user.phone_code_hash = None
            user.phone_code_expires_at = None
            # SEC-03: desativar derruba as sessoes ja emitidas
            from app import auth_guard

            auth_guard.revoke_user_sessions(user.username)
    if payload.email is not None:
        user.email = payload.email
    if payload.phone is not None:
        user.phone = payload.phone
    await session.commit()
    await session.refresh(user)
    return _user_out(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: str, session: SessionReq) -> None:
    user = await _get_user_or_404(session, user_id)
    username = user.username
    await session.delete(user)
    await session.commit()
    from app import auth_guard

    auth_guard.revoke_user_sessions(username)
