"""Rotas /admin/* — CRUD do registro de modelos + metering + dashboards (DA-46/47/48/87/89).

Autenticacao: ADMIN_API_KEY dedicada (header X-API-Admin-Key,
app/admin/security.py) em TODAS as rotas deste router.

Separacao de rotas:
- `/admin/api/*` - Endpoints JSON para CRUD e operações administrativas
- `/admin/metrics/*` - Métricas e dashboards (DA-87)
- `/admin/reports/*` - Relatórios compliance (DA-89)
- `/admin/dashboard/compliance` - Dados para o dashboard de compliance (DA-89)

Sem DATABASE_URL → HTTP 503 "Persistencia nao configurada" (fail-closed:
nenhuma rota admin finge sucesso sem banco por tras).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.correlation import build_system_index, correlate
from app.admin.crypto import decrypt_evidence
from app.admin.models import KnowledgeDocumentVersion, WebUser
from app.admin.repository import AdminRepository
from app.admin.security import verify_admin_key
from app.api_errors import api_error
from app.config import settings
from app.db import get_db_session
from app.llm.origins import canonical_origin
from app.ontology.neo4j_loader import get_error_hierarchy
from app.services.incident_repository import IncidentRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", dependencies=[Depends(verify_admin_key)])

SessionDep = Annotated[AsyncSession | None, Depends(get_db_session)]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


def _origin_or_422(raw: str) -> str:
    """M-11: origem sempre na forma canonica (app/llm/origins.py)."""
    origin = canonical_origin(raw)
    if not origin:
        raise api_error(
            422,
            "invalid_origin",
            f"'{raw}' nao e uma origem: use a URL ou o host do provider "
            "(ex.: api.groq.com, https://<recurso>.openai.azure.com, http://127.0.0.1:11434)",
            value=raw,
        )
    return origin


class ModelCreate(BaseModel):
    provider_origin: str = Field(min_length=1, max_length=128)

    @field_validator("provider_origin")
    @classmethod
    def _canonical(cls, value: str) -> str:
        origin = canonical_origin(value)
        if not origin:
            raise ValueError("nao e uma origem (use URL ou host, ex.: api.groq.com)")
        return origin

    model_id: str = Field(min_length=1, max_length=128)
    base_url: str | None = None
    price_in_per_1m: float | None = Field(default=None, ge=0)
    price_out_per_1m: float | None = Field(default=None, ge=0)
    monthly_limit_tokens: int | None = Field(default=None, ge=1)
    enabled: bool = True
    is_default: bool = False
    notes: str | None = None
    # DA-72: parametros de roteamento (nulo = derivado/herdado)
    transport: Literal["ollama", "openai_compatible", "azure_openai"] | None = None
    context_window_tokens: int | None = Field(default=None, ge=256)
    supports_tools: bool | None = None
    supports_response_format: bool | None = None
    supports_seed: bool | None = None
    domains: list[Literal["sap", "saas", "generic"]] | None = None
    tier: Literal["local", "standard", "premium"] | None = None
    priority: int = Field(default=100, ge=0, le=10_000)

    @field_validator("domains")
    @classmethod
    def _domains(cls, value: list[str] | None) -> list[str] | None:
        # lista vazia = "nenhum dominio" seria um modelo inalcancavel;
        # o operador quer dizer "todos" -> nulo.
        return sorted(set(value)) or None if value is not None else None


class ModelPatch(BaseModel):
    base_url: str | None = None
    price_in_per_1m: float | None = Field(default=None, ge=0)
    price_out_per_1m: float | None = Field(default=None, ge=0)
    monthly_limit_tokens: int | None = Field(default=None, ge=1)
    enabled: bool | None = None
    is_default: bool | None = None
    notes: str | None = None
    # DA-72 (null explicito no PATCH volta ao derivado/herdado)
    transport: Literal["ollama", "openai_compatible", "azure_openai"] | None = None
    context_window_tokens: int | None = Field(default=None, ge=256)
    supports_tools: bool | None = None
    supports_response_format: bool | None = None
    supports_seed: bool | None = None
    domains: list[Literal["sap", "saas", "generic"]] | None = None
    tier: Literal["local", "standard", "premium"] | None = None
    priority: int | None = Field(default=None, ge=0, le=10_000)

    @field_validator("domains")
    @classmethod
    def _domains(cls, value: list[str] | None) -> list[str] | None:
        return sorted(set(value)) or None if value is not None else None


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


class RuntimeConfigPatch(BaseModel):
    llm_registry_db: bool | None = None
    llm_provider: str | None = None
    llm_route: str | None = None
    llm_fallback_provider: str | None = None
    llm_credentials_master_key: str | None = None
    data_sovereignty_mode: str | None = None
    confidential_allowed_origins: str | None = None
    require_auth: bool | None = None
    rule_engine_enabled: bool | None = None
    diagnosis_timeout_seconds: float | None = None
    llm_request_timeout_seconds: float | None = None
    # DA-72: orcamento (DA-67) e pesos do score de roteamento
    diagnosis_budget_usd: float | None = Field(default=None, gt=0)
    llm_budget_reserve_ratio: float | None = Field(default=None, ge=0, le=1)
    llm_gateway_max_cost_usd: float | None = Field(default=None, gt=0)
    llm_routing_cost_weight: float | None = Field(default=None, ge=0)
    llm_routing_latency_weight: float | None = Field(default=None, ge=0)


# Validacao de provider — Literal do .env (invariante: so esses tres sao possiveis)
_VALID_PROVIDERS = frozenset({"ollama", "openai", "azure_openai", ""})


def _require_session(session: SessionDep) -> AsyncSession:
    if session is None:
        raise api_error(503, "db_not_configured", "Persistencia nao configurada")
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
        # DA-72: parametros de roteamento
        "transport": model.transport,
        "context_window_tokens": model.context_window_tokens,
        "supports_tools": model.supports_tools,
        "supports_response_format": model.supports_response_format,
        "supports_seed": model.supports_seed,
        "domains": model.domains,
        "tier": model.tier,
        "priority": model.priority,
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


@router.get("/api/registry/status")
async def registry_status(
    session: SessionDep,
) -> dict[str, Any]:
    """Estado do registro: flags de config e, se banco vivo, contagem.

    Adiciona `runtime_config_source` indicando se os valores de runtime
    vem do banco (override) ou do .env (default)."""
    from app.admin.crypto import master_key_configured
    from app.admin.repository import get_runtime_config as _get
    from app.config import settings

    db = session.get_bind() if hasattr(session, "get_bind") else None
    db_available = db is not None or bool(settings.database_url)

    # Determinar fonte dos valores de runtime
    runtime_source = "env"
    if db_available:
        overrides = _get()
        if overrides and any(v is not None for v in overrides.values()):
            runtime_source = "db"

    result: dict[str, Any] = {
        "managed": settings.llm_registry_db,
        "db_configured": bool(settings.database_url),
        "master_key_configured": master_key_configured(),
        "metering_enabled": settings.metering_enabled,
        "runtime_config_source": runtime_source,
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
# Runtime config (DA-XX: overrides de infraestrutura via admin UI)
# ---------------------------------------------------------------------------


def _env_runtime_values() -> dict[str, Any]:
    """Valores do .env para cada chave editavel (banner DB != .env)."""
    from app.admin.repository import RUNTIME_CONFIG_KEYS

    return {key: getattr(settings, key, None) for key in RUNTIME_CONFIG_KEYS}


@router.get("/api/runtime-config")
async def get_runtime_config(session: SessionDep) -> dict[str, Any]:
    """Overrides de runtime armazenados no banco.

    Com linha no banco, devolve os valores dela e `env_values` (os do .env)
    para a UI comparar. Sem banco ou sem linha (migration nao aplicada),
    devolve os valores do .env e `env_values=None` (nao ha diferenca
    possivel). DA-72 acrescentou orcamento e pesos de roteamento."""
    from app.admin.repository import get_runtime_config as _get

    result = _get()
    if result is not None:
        result["env_values"] = _env_runtime_values()
        return result
    return {**_env_runtime_values(), "env_values": None}


@router.patch("/api/runtime-config")
async def patch_runtime_config(
    payload: RuntimeConfigPatch,
    session: SessionReq,
) -> dict[str, Any]:
    """Atualiza overrides de runtime.

    Campos nao enviados ficam inalterados. Retorna o estado apos a atualizacao."""
    updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    if not updates:
        raise api_error(
            422,
            "empty_update",
            "Envie pelo menos um campo: " + ", ".join(RuntimeConfigPatch.model_fields),
        )

    # GAP-8: validar provider antes de salvar — evita erro genérico em runtime
    if "llm_provider" in updates and updates["llm_provider"] not in _VALID_PROVIDERS:
        raise api_error(
            422,
            "invalid_provider",
            f"llm_provider invalido: '{updates['llm_provider']}'. "
            "Valores possiveis: ollama, openai, azure_openai (ou vazio).",
            value=updates["llm_provider"],
        )

    from app.admin.repository import update_runtime_config as _update

    result = _update(updates)
    if result is None:
        raise api_error(
            503,
            "db_not_configured",
            "Persistencia nao configurada — impossivel salvar overrides de runtime",
        )
    return result


@router.get("/api/effective-config")
async def effective_config(session: SessionDep) -> dict[str, Any]:
    """GAP de usabilidade: exportar configuracao efetiva via UI.

    Combina overrides do banco (llm_runtime_config) com valores atuais do
    .env (mascarados para campos sensiveis). Cada campo mostra sua fonte
    ("db" ou "env") e o valor efetivo (override DB quando presente, senao .env).

    Retorna tambem `sensitive_fields` com a lista de nomes de campos que sao
    tratados como sensiveis (mascarados na saida).
    """
    from app.admin.repository import get_runtime_config as _get
    from app.config import settings

    db = await session.get_bind() if hasattr(session, "get_bind") else None
    db_available = db is not None or bool(settings.database_url)

    # Ler overrides do banco (best-effort)
    overrides: dict[str, Any] | None = _get() if db_available else None

    # Campos sensiveis que devem ser mascarados na saida
    sensitive_fields = frozenset(
        {
            "llm_credentials_master_key",
            "openai_api_key",
            "azure_openai_api_key",
            "sap_password",
            "odata_client_secret",
            "salesforce_client_secret",
            "workday_client_secret",
            "sfsf_client_secret",
            "po_password",
            "po_oauth_client_secret",
            "ariba_client_secret",
            "cap_client_secret",
            "apim_client_secret",
            "admin_api_key",
            "a2a_api_key",
            "event_mesh_api_key",
            "smtp_password",
            "amqp_password",
        }
    )

    # Campos que o runtime_config pode sobrescrever
    config_fields = [
        "llm_registry_db",
        "llm_provider",
        "llm_route",
        "llm_fallback_provider",
        "llm_credentials_master_key",
        "data_sovereignty_mode",
        "confidential_allowed_origins",
        "require_auth",
        "rule_engine_enabled",
        "diagnosis_timeout_seconds",
        "llm_request_timeout_seconds",
    ]

    # Montar a lista de campos com fonte e valor efetivo
    effective: list[dict[str, Any]] = []
    for field in config_fields:
        db_val = overrides.get(field) if overrides else None
        env_val = getattr(settings, field, None)

        # Fonte: "db" se o override existe e nao e' None, senao "env"
        source = "db" if (overrides and db_val is not None) else "env"
        effective_value = db_val if source == "db" else env_val

        # Mascara valores sensiveis
        is_sensitive = field in sensitive_fields
        display_value = _mask_sensitive(effective_value, is_sensitive)

        effective.append(
            {
                "field": field,
                "source": source,
                "value": display_value,
                "raw_db": db_val,  # sempre cru (para comparacao na UI)
                "raw_env": env_val,
                "is_sensitive": is_sensitive,
            }
        )

    return {
        "effective": effective,
        "sensitive_fields": list(sensitive_fields),
        "db_available": db_available,
        "overrides_count": len(overrides) if overrides else 0,
    }


def _mask_sensitive(value: Any, is_sensitive: bool) -> str:
    """Mascara valores sensiveis para exibicao na UI."""
    if not is_sensitive:
        return str(value) if value is not None else "(vazio)"
    s = str(value) if value is not None else ""
    if not s:
        return "(vazio)"
    if len(s) <= 6:
        return "*" * len(s)
    return f"{s[:3]}...{s[-3:]}"


@router.get("/api/runtime-config/check-activate")
async def check_activate_registry(session: SessionDep) -> dict[str, Any]:
    """GAP-2: pré-check antes de ativar LLM_REGISTRY_DB.

    Verifica se ha modelos registrados para a origem que seria usada
    (baseada no provider atual ou no .env). Retorna warnings quando
    a ativacao causaria fail-closed."""
    from app.admin.repository import get_runtime_config as _get
    from app.config import settings

    db = await session.get_bind() if hasattr(session, "get_bind") else None
    db_available = db is not None or bool(settings.database_url)

    # Determinar provider que seria usado
    overrides = _get() if db_available else None
    provider = (overrides.get("llm_provider") if overrides else None) or settings.llm_provider

    result: dict[str, Any] = {
        "provider": provider,
        "db_available": db_available,
        "models_count": 0,
        "enabled_models_count": 0,
        "has_default": False,
        "warnings": [],
    }

    if not db_available:
        result["warnings"].append(
            "Banco nao configurado (DATABASE_URL). Sem banco, o registro de modelos "
            "nao esta disponivel — ativar LLM_REGISTRY_DB causaria fail-closed."
        )
        return result

    try:
        from sqlalchemy import select

        from app.admin.models import LlmModel
        from app.admin.repository import _get_sync_session_factory

        with _get_sync_session_factory()() as db_session:
            all_models = (
                db_session.execute(select(LlmModel).where(LlmModel.provider_origin == provider))
                .scalars()
                .all()
            )
            enabled = [m for m in all_models if m.enabled]
            default = next((m for m in enabled if m.is_default), None)

            result["models_count"] = len(all_models)
            result["enabled_models_count"] = len(enabled)
            result["has_default"] = default is not None

            if not all_models:
                result["warnings"].append(
                    f"Nenhum modelo registrado para a origem '{provider}'. "
                    "Registre um modelo antes de ativar LLM_REGISTRY_DB."
                )
            elif not enabled:
                result["warnings"].append(
                    f"Modelos registrados para '{provider}' existem, mas nenhum esta "
                    "habilitado. Habilite pelo menos um antes de ativar."
                )
            elif not default and len(enabled) > 1:
                result["warnings"].append(
                    f"{len(enabled)} modelos habilitados para '{provider}' sem default. "
                    "Defina um como padrao ou desative os extras para evitar ambiguidade."
                )
    except RuntimeError:
        result["warnings"].append("Erro ao verificar modelos — tente novamente.")

    return result


# ---------------------------------------------------------------------------
# Modelos (DA-46)
# ---------------------------------------------------------------------------


@router.get("/api/models")
async def list_models(session: SessionReq) -> list[dict[str, Any]]:
    repo = AdminRepository(session)
    models = await repo.list_models()
    out: list[dict[str, Any]] = []
    for model in models:
        usage = await repo.get_open_usage(model.provider_origin, model.model_id)
        out.append(_model_out(model, usage))
    return out


@router.post("/api/models", status_code=status.HTTP_201_CREATED)
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
        raise api_error(
            409,
            "model_exists",
            f"Modelo '{payload.model_id}' ja registrado na origem '{payload.provider_origin}'",
            model=payload.model_id,
            origin=payload.provider_origin,
        )
    model = await repo.create_model(**payload.model_dump())
    return _model_out(model, None)


@router.get("/api/models/{model_id}")
async def get_model(
    model_id: str,
    session: SessionReq,
) -> dict[str, Any]:
    repo = AdminRepository(session)
    model = await repo.get_model(model_id)
    if model is None:
        raise api_error(404, "model_not_found", "Modelo nao encontrado")
    usage = await repo.get_open_usage(model.provider_origin, model.model_id)
    return _model_out(model, usage)


@router.patch("/api/models/{model_id}")
async def patch_model(
    model_id: str,
    payload: ModelPatch,
    session: SessionReq,
) -> dict[str, Any]:
    repo = AdminRepository(session)
    model = await repo.update_model(model_id, payload.model_dump(exclude_unset=True))
    if model is None:
        raise api_error(404, "model_not_found", "Modelo nao encontrado")
    usage = await repo.get_open_usage(model.provider_origin, model.model_id)
    return _model_out(model, usage)


@router.delete("/api/models/{model_id}")
async def delete_model(
    model_id: str,
    session: SessionReq,
) -> dict[str, Any]:
    repo = AdminRepository(session)
    deleted = await repo.delete_model(model_id)
    if not deleted:
        raise api_error(404, "model_not_found", "Modelo nao encontrado")
    return {"status": "deleted", "id": model_id}


@router.get("/api/routing/preview")
async def routing_preview(
    session: SessionDep,
    sensitivity: Literal["public", "confidential"] = "confidential",
    domain: Literal["sap", "saas", "generic"] | None = None,
    tokens: int = Query(default=4000, ge=1, le=2_000_000),
) -> dict[str, Any]:
    """DA-72: simula a decisao do Policy Engine para um diagnostico novo, sem
    chamar o LLM. Mesma funcao que o gateway usa (app/llm/gateway.py::
    preview_routing). Nao decifra credencial nenhuma."""
    from app.llm.gateway import preview_routing

    decision, cfg = preview_routing(sensitivity, domain, tokens)
    weights = {
        "cost": cfg.llm_routing_cost_weight,
        "latency": cfg.llm_routing_latency_weight,
    }
    return {
        "mode": "registry" if decision.request.registry is not None else "env",
        "sensitivity": sensitivity,
        "domain": domain,
        "context_tokens": tokens,
        "budget_remaining_usd": round(decision.request.budget_remaining_usd, 6),
        "weights": weights,
        "selected_provider": decision.selected_provider,
        "selected_reason": decision.selected_reason,
        "all_rejected": decision.all_rejected,
        "candidates": [
            {
                "rank": i + 1 if c.allowed else None,
                "provider": c.provider,
                "model_id": c.model_id,
                "origin": c.origin,
                "locality": c.locality,
                "tier": c.tier,
                "allowed": c.allowed,
                "reasons": [r.value for r in c.rejection_reasons],
                "detail": list(c.detail),
                "detail_items": list(c.detail_items),  # DA-74: para a UI traduzir
                "estimated_cost_usd": round(c.estimated_cost_usd, 6),
                "latency_ms": c.latency_ms,
                "score": c.score,
                "priority": c.priority,
                "is_default": c.is_default,
            }
            for i, c in enumerate(decision.candidates)
        ],
    }


# ---------------------------------------------------------------------------
# Credenciais (DA-47)
# ---------------------------------------------------------------------------


@router.get("/api/credentials")
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


@router.put("/api/credentials/{provider_origin:path}")
async def set_credential(
    provider_origin: str,
    payload: CredentialIn,
    session: SessionReq,
) -> dict[str, Any]:
    from app.admin.crypto import master_key_configured

    if not master_key_configured():
        raise api_error(
            503,
            "master_key_missing",
            "LLM_CREDENTIALS_MASTER_KEY nao configurada — impossivel cifrar "
            "a credencial (DA-47). Configure a master key no .env.",
        )
    provider_origin = _origin_or_422(provider_origin)
    repo = AdminRepository(session)
    cred = await repo.set_credential(provider_origin, payload.key)
    return {
        "provider_origin": cred.provider_origin,
        "masked": cred.masked,
        "key_version": cred.key_version,
    }


@router.delete("/api/credentials/{provider_origin:path}")
async def delete_credential(
    provider_origin: str,
    session: SessionReq,
) -> dict[str, Any]:
    provider_origin = _origin_or_422(provider_origin)
    repo = AdminRepository(session)
    deleted = await repo.delete_credential(provider_origin)
    if not deleted:
        raise api_error(404, "credential_not_found", "Credencial nao encontrada")
    return {"status": "deleted", "provider_origin": provider_origin}


# ---------------------------------------------------------------------------
# Metering (DA-48)
# ---------------------------------------------------------------------------


@router.get("/api/usage")
async def usage_summary(session: SessionReq) -> list[dict[str, Any]]:
    repo = AdminRepository(session)
    return await repo.usage_summary()


@router.post("/api/usage/{provider_origin:path}/{model_id}/reset")
async def reset_usage(
    provider_origin: str,
    model_id: str,
    session: SessionReq,
) -> dict[str, Any]:
    provider_origin = _origin_or_422(provider_origin)
    repo = AdminRepository(session)
    new_period = await repo.reset_usage_period(provider_origin, model_id)
    if new_period is None:
        raise api_error(
            404,
            "usage_period_not_found",
            f"Nenhum periodo aberto de uso para '{model_id}' na origem '{provider_origin}'",
            model=model_id,
            origin=provider_origin,
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
        raise api_error(422, "invalid_system_fields", "; ".join(bad), details="; ".join(bad))


@router.get("/api/systems")
async def list_systems(session: SessionReq) -> list[dict[str, Any]]:
    repo = AdminRepository(session)
    return [_system_out(s) for s in await repo.list_systems()]


@router.post("/api/systems", status_code=status.HTTP_201_CREATED)
async def create_system(
    payload: SystemCreate,
    session: SessionReq,
) -> dict[str, Any]:
    _validate_system_values(payload.connector_type, payload.environment, payload.status)
    repo = AdminRepository(session)
    if await repo.get_system(key=payload.system_key) is not None:
        raise api_error(
            409,
            "system_exists",
            f"system_key '{payload.system_key}' ja registrado",
            key=payload.system_key,
        )
    system = await repo.create_system(**payload.model_dump())
    return _system_out(system)


@router.get("/api/systems/{system_id}")
async def get_system(
    system_id: str,
    session: SessionReq,
) -> dict[str, Any]:
    repo = AdminRepository(session)
    system = await repo.get_system(system_id)
    if system is None:
        raise api_error(404, "system_not_found", "Sistema nao encontrado")
    return _system_out(system)


@router.patch("/api/systems/{system_id}")
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
        raise api_error(404, "system_not_found", "Sistema nao encontrado")
    return _system_out(system)


@router.delete("/api/systems/{system_id}")
async def delete_system(
    system_id: str,
    session: SessionReq,
) -> dict[str, Any]:
    repo = AdminRepository(session)
    deleted = await repo.delete_system(system_id)
    if not deleted:
        raise api_error(404, "system_not_found", "Sistema nao encontrado")
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


@router.get("/api/web-search-sources")
async def list_web_search_sources(session: SessionReq) -> list[dict[str, Any]]:
    repo = AdminRepository(session)
    return [_source_out(s) for s in await repo.list_web_search_sources()]


@router.post("/api/web-search-sources", status_code=status.HTTP_201_CREATED)
async def create_web_search_source(
    payload: WebSearchSourceCreate,
    session: SessionReq,
) -> dict[str, Any]:
    _validate_source_interface_type(payload.interface_type)
    repo = AdminRepository(session)
    if await repo.get_web_search_source(interface_type=payload.interface_type) is not None:
        raise api_error(
            409,
            "source_exists",
            f"interface_type '{payload.interface_type}' ja tem fonte cadastrada",
            interface=payload.interface_type,
        )
    try:
        source = await repo.create_web_search_source(**payload.model_dump())
        await session.commit()
    except IntegrityError:
        # O pre-check acima tem janela: dois admins criando o mesmo
        # interface_type ao mesmo tempo passam os dois. Quem decide e' a
        # constraint UNIQUE — sem este catch a segunda viraria 500.
        await session.rollback()
        raise api_error(
            409,
            "source_exists",
            f"interface_type '{payload.interface_type}' ja tem fonte cadastrada",
            interface=payload.interface_type,
        ) from None
    return _source_out(source)


@router.get("/api/web-search-sources/{source_id}")
async def get_web_search_source(source_id: str, session: SessionReq) -> dict[str, Any]:
    repo = AdminRepository(session)
    source = await repo.get_web_search_source(source_id)
    if source is None:
        raise api_error(404, "source_not_found", "Fonte de busca nao encontrada")
    return _source_out(source)


@router.patch("/api/web-search-sources/{source_id}")
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
            raise api_error(
                422,
                "source_field_empty",
                f"{key} nao pode ficar vazio: sem ele a linha e' ignorada "
                "pela resolucao fail-closed",
                field=key,
            )
    repo = AdminRepository(session)
    source = await repo.update_web_search_source(source_id, updates)
    if source is None:
        raise api_error(404, "source_not_found", "Fonte de busca nao encontrada")
    await session.commit()
    return _source_out(source)


@router.delete("/api/web-search-sources/{source_id}")
async def delete_web_search_source(source_id: str, session: SessionReq) -> dict[str, Any]:
    repo = AdminRepository(session)
    if not await repo.delete_web_search_source(source_id):
        raise api_error(404, "source_not_found", "Fonte de busca nao encontrada")
    await session.commit()
    return {"status": "deleted", "id": source_id}


@router.get("/api/systems/{system_id}/incidents")
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
        raise api_error(404, "system_not_found", "Sistema nao encontrado")

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
        # DA-53: proveniencia; DA-44/DA-67: escalonamento e custo (migration 019)
        "llm_model": incident.llm_model,
        "prompt_version": incident.prompt_version,
        "prompt_digest": incident.prompt_digest,
        "should_escalate": incident.should_escalate,
        "escalation_reason": incident.escalation_reason,
        "cost_usd": incident.cost_usd,
        "llm_calls": incident.llm_calls,
        "locale": getattr(incident, "locale", None),  # DA-74 (migration 021)
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


@router.get("/api/incidents")
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


@router.get("/api/incidents/{incident_id}")
async def get_incident(incident_id: str, session: SessionReq) -> dict[str, Any]:
    index = build_system_index(await AdminRepository(session).list_systems())
    incident = await IncidentRepository(session).get_by_id(incident_id)
    if incident is None:
        raise api_error(404, "incident_not_found", "Incidente nao encontrado")
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
    # DA-74: idioma das mensagens de ativacao. Ausente = o da Admin UI de
    # quem cadastra (cookie iic_locale / Accept-Language).
    locale: str | None = Field(default=None, max_length=16)


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
        raise api_error(404, "user_not_found", "Usuario nao encontrado") from None
    user = await session.get(WebUser, uid)
    if user is None:
        raise api_error(404, "user_not_found", "Usuario nao encontrado")
    return user


@router.get("/api/users")
async def list_users(session: SessionReq) -> list[dict[str, Any]]:
    from sqlalchemy import select

    users = (await session.scalars(select(WebUser).order_by(WebUser.created_at))).all()
    return [_user_out(u) for u in users]


@router.post("/api/users", status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreate, session: SessionReq, request: Request) -> dict[str, Any]:
    """Cria usuario PENDENTE de e-mail. A senha inicial entra como PBKDF2
    (mesmo formato DA-54) — nunca em claro em lugar nenhum.

    Out-of-band (SMTP nao configurado): o token de ativacao volta AQUI,
    na resposta de admin (canal X-API-Admin-Key), com WARNING no log.
    Entrega real: `deliver_email` em app/webusers.py — token sai da
    resposta sozinho quando o canal entrega de verdade."""
    from app.i18n import locale_from_request, normalize_locale
    from app.i18n.content import CONTENT_LOCALES
    from app.webusers import create_user as domain_create_user

    locale = normalize_locale(payload.locale) if payload.locale else locale_from_request(request)
    if locale not in CONTENT_LOCALES:
        if payload.locale:
            raise api_error(
                422,
                "invalid_locale",
                f"locale nao suportado: {payload.locale}",
                locale=payload.locale,
            )
        locale = None  # pseudo-locale de revisao: mensagens no default
    try:
        user, tokens = await domain_create_user(
            session,
            username=payload.username,
            email=payload.email,
            phone=payload.phone,
            password=payload.password,
            created_by="admin-api",
            locale=locale,
        )
    except ValueError as exc:
        raise api_error(409, "user_exists", str(exc), username=payload.username) from None
    out = _user_out(user)
    out["activation"] = {
        "email_token": tokens.email_token,  # None quando entregue de verdade
        "email_delivered": tokens.email_delivered,
        "next_step": "email",
    }
    return out


@router.post("/api/users/{user_id}/email-token")
async def reissue_email_token(user_id: str, session: SessionReq) -> dict[str, Any]:
    """Reemite o token da etapa 1 (so' para pendentes de e-mail)."""
    from app.webusers import (
        EMAIL_TOKEN_TTL_SECONDS,
        activation_email_text,
        deliver_email,
        sign_email_token,
    )

    user = await _get_user_or_404(session, user_id)
    if user.status != "pending_email":
        raise api_error(
            409,
            "reissue_requires_pending_email",
            f"reemissao de token de e-mail exige status pending_email (atual: {user.status})",
            status=user.status,
        )
    token = sign_email_token(user.username, EMAIL_TOKEN_TTL_SECONDS, settings.session_secret)
    subject, body = activation_email_text(token, user.locale)
    result = deliver_email(user.email, body, subject)
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


@router.post("/api/users/{user_id}/phone-code")
async def reissue_phone_code(user_id: str, session: SessionReq) -> dict[str, Any]:
    """Reemite o codigo da etapa 2 (so' para pendentes de telefone).

    Out-of-band: o codigo volta AQUI (admin), nunca em rota publica."""
    from datetime import UTC, datetime, timedelta

    from app.webusers import (
        PHONE_CODE_TTL_SECONDS,
        activation_sms_text,
        deliver_sms,
        generate_phone_code,
        hash_phone_code,
    )

    user = await _get_user_or_404(session, user_id)
    if user.status != "pending_phone":
        raise api_error(
            409,
            "reissue_requires_pending_phone",
            f"reemissao de codigo exige status pending_phone (atual: {user.status})",
            status=user.status,
        )
    code = generate_phone_code()
    user.phone_code_hash = hash_phone_code(code, settings.session_secret)
    user.phone_code_expires_at = datetime.now(UTC) + timedelta(seconds=PHONE_CODE_TTL_SECONDS)
    user.phone_code_attempts = 0
    result = deliver_sms(user.phone, activation_sms_text(code, user.locale))
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


@router.patch("/api/users/{user_id}")
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
            raise api_error(422, "invalid_user_status", "status aceita: active, disabled")
        if user.status.startswith("pending") and payload.status != "disabled":
            raise api_error(
                422,
                "pending_user_change",
                "usuario pendente so pode ser desativado ou ter as etapas reemitidas",
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


@router.delete("/api/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: str, session: SessionReq) -> None:
    user = await _get_user_or_404(session, user_id)
    username = user.username
    await session.delete(user)
    await session.commit()
    from app import auth_guard

    auth_guard.revoke_user_sessions(username)


class ErrorHierarchyResponse(BaseModel):
    uri: str
    broader: list[str]
    narrower: list[str]


@router.get("/ontology/hierarchy", response_model=ErrorHierarchyResponse)
async def get_ontology_hierarchy(
    uri: str = Query(..., description="Error concept URI (e.g., urn:error:OAuth2)"),
) -> dict[str, Any]:
    """Retrieve SKOS hierarchy (broader/narrower) for an error concept from Neo4j.

    Returns the error concept URI along with its broader and narrower error concepts
    based on the SKOS taxonomy loaded into Neo4j.

    Returns 404 if the error concept is not found in Neo4j.
    Returns 503 if Neo4j is not configured or unavailable.
    """
    if not settings.neo4j_uri or not settings.neo4j_user or not settings.neo4j_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Neo4j service unavailable",
        )

    try:
        result = await get_error_hierarchy(
            settings.neo4j_uri,
            settings.neo4j_user,
            settings.neo4j_password,
            uri,
        )
    except Exception as exc:
        logger.warning("Neo4j query failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Neo4j service unavailable",
        ) from exc

    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Error concept not found")

    return {
        "uri": result[0]["root_uri"],
        "broader": result[0]["broader"],
        "narrower": result[0]["narrower"],
    }


# ---------------------------------------------------------------------------
# DA-86: versionamento de documentos RAG (UI /admin/knowledge/documents)
# ---------------------------------------------------------------------------


class KnowledgeVersionCreate(BaseModel):
    """Payload de criação de uma versão de documento (DA-86)."""

    content: str = Field(min_length=1)
    is_current: bool = False


def _version_to_dict(version: KnowledgeDocumentVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "document_id": version.document_id,
        "version": version.version,
        "content": version.content,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "is_current": version.is_current,
    }


async def _load_versions(session: AsyncSession, document_id: str) -> list[KnowledgeDocumentVersion]:
    result = await session.execute(
        select(KnowledgeDocumentVersion)
        .where(KnowledgeDocumentVersion.document_id == document_id)
        .order_by(KnowledgeDocumentVersion.version)
    )
    return list(result.scalars().all())


@router.post("/api/knowledge/documents/{document_id}/versions", status_code=201)
async def create_knowledge_version(
    document_id: str, payload: KnowledgeVersionCreate, session: SessionDep
) -> dict[str, Any]:
    """DA-86: cria a próxima versão de um documento de conhecimento."""
    if session is None:
        raise api_error(503, "db_not_configured", "Persistencia nao configurada")

    existing = await _load_versions(session, document_id)
    next_version = (max((v.version for v in existing), default=0)) + 1

    if payload.is_current:
        for version in existing:
            version.is_current = False

    row = KnowledgeDocumentVersion(
        document_id=document_id,
        version=next_version,
        content=payload.content,
        is_current=payload.is_current,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise api_error(
            409,
            "version_exists",
            f"versão {next_version} já existe para o documento {document_id}",
        ) from None

    await session.refresh(row)
    return _version_to_dict(row)


@router.get("/api/knowledge/documents/{document_id}/versions")
async def list_knowledge_versions(document_id: str, session: SessionDep) -> dict[str, Any]:
    """DA-86: lista as versões de um documento e qual está ativa."""
    if session is None:
        raise api_error(503, "db_not_configured", "Persistencia nao configurada")

    versions = await _load_versions(session, document_id)
    if not versions:
        raise api_error(404, "document_not_found", f"documento {document_id} não encontrado")

    current = next((v.version for v in versions if v.is_current), None)
    return {
        "document_id": document_id,
        "versions": [_version_to_dict(v) for v in versions],
        "current_version": current,
    }


@router.get("/api/knowledge/documents/{document_id}/history")
async def knowledge_version_history(document_id: str, session: SessionDep) -> dict[str, Any]:
    """DA-86: histórico completo de versões (somente leitura)."""
    if session is None:
        raise api_error(503, "db_not_configured", "Persistencia nao configurada")

    versions = await _load_versions(session, document_id)
    if not versions:
        raise api_error(404, "document_not_found", f"documento {document_id} não encontrado")

    return {
        "document_id": document_id,
        "versions": [_version_to_dict(v) for v in versions],
    }


@router.post("/api/knowledge/documents/{document_id}/rollback/{version}")
async def rollback_knowledge_version(
    document_id: str, version: int, session: SessionDep
) -> dict[str, Any]:
    """DA-86: marca uma versão anterior como ativa (rollback)."""
    if session is None:
        raise api_error(503, "db_not_configured", "Persistencia nao configurada")

    versions = await _load_versions(session, document_id)
    if not versions:
        raise api_error(404, "document_not_found", f"documento {document_id} não encontrado")

    if not any(v.version == version for v in versions):
        raise api_error(
            404, "version_not_found", f"versão {version} não encontrada para {document_id}"
        )

    for v in versions:
        v.is_current = v.version == version
    await session.commit()

    return {"document_id": document_id, "current_version": version}


# ---------------------------------------------------------------------------
# DA-87: Métricas por domínio (dashboard)
# ---------------------------------------------------------------------------


@router.get("/metrics/domains")
async def get_domain_metrics(
    session: SessionReq,
    days: int = Query(default=30, ge=1, le=365, description="Janela em dias"),
) -> dict[str, Any]:
    """DA-87: Métricas por domínio (sap/saas/generic) para dashboards.

    Janela: últimos ``days`` dias (default 30), limitada aos 1000 incidentes
    mais recentes. Retorna count, hit@1, latência média e custo por domínio.
    """

    from app.admin.metrics import build_dashboard_data

    repo = IncidentRepository(session)
    since = datetime.now(UTC) - timedelta(days=days)
    incidents = await repo.list_recent(limit=1000, since=since)

    # Transforma em dicts
    incidents_data = [
        {
            "agent_domain": i.agent_domain,
            "diagnosis_correct": i.diagnosis_correct,
            "latency_ms": i.latency_ms,
            "cost_usd": i.cost_usd,
        }
        for i in incidents
    ]

    return build_dashboard_data(incidents_data)


# ---------------------------------------------------------------------------
# DA-89: Relatórios compliance export
# ---------------------------------------------------------------------------


@router.get("/reports/compliance/export")
async def export_compliance_report(
    session: SessionDep,
    format: Literal["pdf", "xlsx"] = Query(..., description="Formato de exportação"),
    days: int = Query(default=7, ge=1, le=365, description="Número de dias para filtro"),
) -> Response:
    """DA-89: Exportar relatório compliance (PDF ou XLSX).

    Gera relatório com:
        - Resumo de incidentes
        - Métricas de qualidade (Hit@1, accuracy)
        - Eventos auditáveis
        - Custos por domínio

    ``format`` é validado pelo FastAPI antes da dependency de banco, então
    formatos inválidos retornam 422 mesmo sem DATABASE_URL configurado.
    """
    from app.admin.reports import (
        PDF_MEDIA_TYPE,
        XLSX_MEDIA_TYPE,
        build_compliance_summary,
        calculate_quality_metrics,
        export_pdf,
        export_xlsx,
        extract_auditable_events,
        get_compliance_data,
    )

    if session is None:
        raise api_error(503, "db_not_configured", "Persistência não configurada")

    # Busca dados do banco
    compliance_data = await get_compliance_data(session, days=days)

    if compliance_data.get("error"):
        raise api_error(503, "db_error", f"Erro ao buscar dados: {compliance_data['error']}")

    incidents = compliance_data.get("incidents", [])

    # Constrói relatório
    summary = build_compliance_summary(incidents)
    metrics = calculate_quality_metrics(incidents)
    auditable_events = extract_auditable_events(incidents)

    report_data = {
        "summary": summary,
        "metrics": metrics,
        "by_domain": summary["by_domain"],
        "auditable_events": auditable_events,
        "generated_at": compliance_data.get("generated_at"),
    }

    # Exporta no formato solicitado
    if format == "pdf":
        return Response(content=export_pdf(report_data), media_type=PDF_MEDIA_TYPE)

    return Response(content=export_xlsx(report_data), media_type=XLSX_MEDIA_TYPE)


@router.get("/dashboard/compliance")
async def get_compliance_dashboard(session: SessionDep) -> dict[str, Any]:
    """DA-89: Visualização HTML para dashboard de compliance.

    Retorna dados estruturados para renderização no frontend.
    """
    from app.admin.reports import build_compliance_summary, calculate_quality_metrics

    if session is None:
        raise api_error(503, "db_not_configured", "Persistência não configurada")

    repo = IncidentRepository(session)
    incidents = await repo.list_recent(limit=1000)

    incidents_data = [
        {
            "id": str(i.id),
            "created_at": i.created_at,
            "agent_domain": i.agent_domain,
            "probable_root_cause": i.probable_root_cause,
            "model_confidence": i.model_confidence,
            "diagnosis_correct": i.diagnosis_correct,
            "verified_at": i.verified_at,
            "cost_usd": i.cost_usd,
        }
        for i in incidents
    ]

    summary = build_compliance_summary(incidents_data)
    metrics = calculate_quality_metrics(incidents_data)

    return {
        "summary": summary,
        "metrics": metrics,
        "generated_at": datetime.now(UTC).isoformat(),
    }
