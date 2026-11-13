"""DA-72: roteamento por registro de modelos - dados e configuracao.

O Policy Engine (app/llm/routing.py) continua puro: recebe um
`RegistrySnapshot` pronto. Este modulo e' quem le o banco (via
app/admin/repository.py) e quem traduz um modelo do registro em `Settings`
de chamada - transporte, destino final e credencial.

Regras de derivacao (coluna nula = derivado, nunca "liberado"):
- transporte: Ollama se a origem for a do OLLAMA_HOST ou porta 11434;
  Azure OpenAI se o host terminar em .openai.azure.com; senao
  openai_compatible (OpenAI, Groq, vLLM, LiteLLM...).
- destino: base_url do registro; sem ele, a propria origem (+ /v1 no
  openai_compatible). Nunca o base_url do .env: um modelo da Groq sem
  base_url iria para a OpenAI.
- nivel: local se a origem for loopback (DA-43), senao standard.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.config import Settings
from app.llm.origins import canonical_origin, is_loopback_origin, normalize_origin

logger = logging.getLogger(__name__)

TRANSPORTS = ("ollama", "openai_compatible", "azure_openai")
TIERS = ("local", "standard", "premium")
DOMAINS = ("sap", "saas", "generic")

# rotulo de provider que app/llm/factory.py::get_chat_model entende
_TRANSPORT_LABEL = {
    "ollama": "ollama",
    "openai_compatible": "openai",
    "azure_openai": "azure_openai",
}


@dataclass(frozen=True)
class RegistryModel:
    """Um modelo habilitado do registro, como o Policy Engine o enxerga."""

    id: str
    provider_origin: str
    model_id: str
    base_url: str | None = None
    price_in_per_1m: float | None = None
    price_out_per_1m: float | None = None
    monthly_limit_tokens: int | None = None
    is_default: bool = False
    transport: str | None = None
    context_window_tokens: int | None = None
    supports_tools: bool | None = None
    supports_response_format: bool | None = None
    supports_seed: bool | None = None
    domains: tuple[str, ...] | None = None
    tier: str | None = None
    priority: int = 100


@dataclass(frozen=True)
class RegistrySnapshot:
    """Tudo que a decisao precisa, lido de uma vez (sem I/O no engine)."""

    models: tuple[RegistryModel, ...]
    credentialed_origins: frozenset[str] = frozenset()
    usage_tokens: dict[tuple[str, str], int] = field(default_factory=dict)
    latency_ms: dict[tuple[str, str], float] = field(default_factory=dict)


def _model_from_row(row: dict) -> RegistryModel:
    domains = row.get("domains")
    return RegistryModel(
        id=str(row["id"]),
        provider_origin=row["provider_origin"],
        model_id=row["model_id"],
        base_url=row.get("base_url"),
        price_in_per_1m=row.get("price_in_per_1m"),
        price_out_per_1m=row.get("price_out_per_1m"),
        monthly_limit_tokens=row.get("monthly_limit_tokens"),
        is_default=bool(row.get("is_default")),
        transport=row.get("transport"),
        context_window_tokens=row.get("context_window_tokens"),
        supports_tools=row.get("supports_tools"),
        supports_response_format=row.get("supports_response_format"),
        supports_seed=row.get("supports_seed"),
        domains=tuple(domains) if domains else None,
        tier=row.get("tier"),
        priority=int(row.get("priority") if row.get("priority") is not None else 100),
    )


def load_registry_snapshot() -> RegistrySnapshot | None:
    """Le o registro. None = registro indisponivel (sem banco ou erro): o
    gateway trata como nenhum candidato elegivel - fail-closed (DA-46)."""
    from app.admin.repository import (
        credentialed_origins,
        latency_medians,
        load_registry_models,
        open_usage_tokens,
    )

    rows = load_registry_models()
    if rows is None:
        return None
    return RegistrySnapshot(
        models=tuple(_model_from_row(r) for r in rows),
        credentialed_origins=frozenset(credentialed_origins()),
        usage_tokens=open_usage_tokens(),
        latency_ms=latency_medians(),
    )


def effective_transport(model: RegistryModel, cfg: Settings) -> str:
    if model.transport in TRANSPORTS:
        return model.transport
    origin = model.provider_origin
    host = (urlparse(origin).hostname or "").lower()
    port = urlparse(origin).port
    if origin == normalize_origin(cfg.ollama_host) or port == 11434:
        return "ollama"
    if host.endswith(".openai.azure.com"):
        return "azure_openai"
    return "openai_compatible"


def effective_tier(model: RegistryModel) -> str:
    if model.tier in TIERS:
        return model.tier
    return "local" if is_loopback_origin(model.provider_origin) else "standard"


def provider_label(transport: str) -> str:
    return _TRANSPORT_LABEL[transport]


def destination_url(model: RegistryModel, transport: str) -> str:
    """URL final da chamada. Sem base_url, a propria origem do registro."""
    if model.base_url:
        return model.base_url
    if transport == "openai_compatible":
        return model.provider_origin.rstrip("/") + "/v1"
    return model.provider_origin


def candidate_settings(
    model: RegistryModel, cfg: Settings, api_key: str | None = None
) -> tuple[str, Settings]:
    """(rotulo do provider, Settings da chamada) para este modelo.

    Mesmos campos que app/llm/factory.py::_apply_registry_resolution
    sobrescreve, para get_chat_model ler os atributos de sempre."""
    transport = effective_transport(model, cfg)
    label = provider_label(transport)
    url = destination_url(model, transport)
    update: dict = {"llm_provider": label, "llm_fallback_provider": None}
    if transport == "ollama":
        update.update({"llm_model": model.model_id, "ollama_host": url})
    elif transport == "openai_compatible":
        update.update({"llm_model": model.model_id, "openai_base_url": url})
        if api_key:
            update["openai_api_key"] = api_key
    else:
        update.update({"azure_openai_deployment": model.model_id, "azure_openai_endpoint": url})
        if api_key:
            update["azure_openai_api_key"] = api_key
    return label, cfg.model_copy(update=update)


def destination_matches_origin(model: RegistryModel, final_origin: str) -> bool:
    """O destino final tem que ser a origem cadastrada: base_url apontando
    para outro host nao herda a credencial nem a decisao de soberania da
    origem do registro (GOV-01)."""
    return (canonical_origin(final_origin) or final_origin) == model.provider_origin


# ---------------------------------------------------------------------------
# Overrides de runtime (llm_runtime_config) relevantes para a decisao
# ---------------------------------------------------------------------------

_ROUTING_OVERRIDE_KEYS = (
    "llm_registry_db",
    "llm_provider",
    "llm_fallback_provider",
    "data_sovereignty_mode",
    "confidential_allowed_origins",
    "llm_request_timeout_seconds",
    "diagnosis_budget_usd",
    "llm_budget_reserve_ratio",
    "llm_gateway_max_cost_usd",
    "llm_routing_cost_weight",
    "llm_routing_latency_weight",
)


def effective_settings(cfg: Settings) -> Settings:
    """`cfg` com os overrides NAO nulos da Admin UI aplicados.

    Diferente de factory._resolve_runtime_overrides, preserva todos os demais
    campos de `cfg` (model_copy, nao Settings novo). Sem banco ou sem linha,
    devolve `cfg` intacto."""
    from app.admin.repository import get_runtime_config

    if not cfg.database_url:
        return cfg
    overrides = get_runtime_config()
    if not overrides:
        return cfg
    update = {k: overrides[k] for k in _ROUTING_OVERRIDE_KEYS if overrides.get(k) is not None}
    return cfg.model_copy(update=update) if update else cfg
