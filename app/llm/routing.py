"""DA-66: Policy Engine puro para roteamento LLM.

Este modulo extrai da invoke_via_gateway() um Policy Engine puro que
aceita RoutingRequest e devolve RoutingDecision. O objetivo e tornar a
decisao de roteamento explicita, auditavel e testavel sem depender de
execucao completa do gateway.

Fases:
  1. Elegibilidade rigida: soberania/origin, capability exigida, janela de
     contexto, circuito aberto, orçamento.
  2. Scoring de preferencias: custo, latencia observada.

Pesos em config, nunca hardcoded.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from app.config import Settings, settings
from app.llm.capabilities import ProviderCapabilities, capabilities_for_origin
from app.llm.origins import is_loopback_origin, normalize_origin, resolve_provider_origin
from app.llm.registry_routing import RegistrySnapshot

logger = logging.getLogger(__name__)

Sensitivity = Literal["confidential", "public"]
Locality = Literal["local", "cloud"]


class RejectionReason(Enum):
    """Motivos enumerados para rejeicao de um provider na decisao de roteamento."""

    # Elegibilidade rigida
    NO_ORIGIN_RESOLVED = "origin_nao_resolvida"
    SOVEREIGNTY_BLOCKED = "soberania_bloqueada"
    CAPABILITY_MISSING = "capability_faltando"
    CIRCUIT_OPEN = "circuito_aberto"
    BUDGET_EXCEEDED = "orcamento_excedido"
    # DA-72: roteamento por registro de modelos
    CONTEXT_WINDOW = "janela_insuficiente"
    DOMAIN_MISMATCH = "dominio_nao_atendido"
    TIER_NOT_AUTHORIZED = "nivel_nao_autorizado"
    MONTHLY_LIMIT = "limite_mensal_atingido"
    CREDENTIAL_MISSING = "credencial_ausente"
    DESTINATION_MISMATCH = "destino_fora_da_origem"

    # Scoring de preferencias (nunca rejeita, so desclassifica)
    HIGH_COST = "custo_elevado"
    HIGH_LATENCY = "latencia_elevada"


@dataclass(frozen=True)
class RoutingRequest:
    """Entrada da decisao de roteamento."""

    sensitivity: Sensitivity
    task: str  # nome do node/tarefa (ex: "sap_diagnosis", "saas_diagnosis")
    context_tokens: int  # estimativa de tokens de contexto
    budget_remaining_usd: float  # orçamento restante para esta tarefa
    required_capabilities: ProviderCapabilities  # capacidades exigidas
    primary_provider: str
    fallback_provider: str | None
    config: Settings
    # Override do nome do modelo (o mesmo `model_name` que o gateway repassa
    # a get_chat_model). None = o modelo configurado de cada provider.
    model_name: str | None = None
    # DA-72: dominio do sub-agente (supervisor) e o registro lido pelo
    # gateway. Com `registry`, os candidatos sao os modelos habilitados.
    domain: str | None = None
    registry: RegistrySnapshot | None = None


@dataclass(frozen=True)
class Candidate:
    """Um provider candidato na decisao de roteamento."""

    provider: str
    origin: str
    locality: Locality
    allowed: bool
    rejection_reasons: list[RejectionReason]
    estimated_cost_usd: float
    latency_ms: float | None  # None = sem dados historicos
    # DA-72: preenchidos so no roteamento por registro
    model_id: str | None = None
    registry_id: str | None = None
    tier: str | None = None
    priority: int | None = None
    is_default: bool = False
    score: float | None = None
    detail: tuple[str, ...] = ()  # motivo legivel por rejeicao (preview/audit)


@dataclass(frozen=True)
class RoutingDecision:
    """Decisao de roteamento: candidatos ordenados e rejeitados com motivo."""

    request: RoutingRequest
    candidates: list[Candidate]
    selected_provider: str | None  # None = todos rejeitados
    selected_reason: str | None  # motivo da selecao (para audit log)
    all_rejected: bool
    rejection_summary: dict[str, list[str]]  # provider -> lista de motivos

    def as_log_fields(self) -> dict:
        """Campos para audit log estruturado."""
        return {
            "decision_type": "routing",
            "sensitivity": self.request.sensitivity,
            "task": self.request.task,
            "budget_remaining_usd": self.request.budget_remaining_usd,
            "selected_provider": self.selected_provider,
            "all_rejected": self.all_rejected,
            "candidate_count": len(self.candidates),
            "selected_model": next(
                (
                    c.model_id
                    for c in self.candidates
                    if c.provider == self.selected_provider and c.allowed
                ),
                None,
            ),
            "rejection_summary": {
                p: [r.value for r in rs] for p, rs in self.rejection_summary.items()
            },
        }


# Pesos configuraveis para scoring de preferencias
def _get_scoring_weights(cfg: Settings) -> dict[str, float]:
    """Pesos para scoring de preferencias (custo vs latencia)."""
    # DA-66: pesos em config, nunca hardcoded. Valores padrao:
    # - 0.7 = 70% peso para custo
    # - 0.3 = 30% peso para latencia
    return {
        "cost_weight": cfg.llm_routing_cost_weight,
        "latency_weight": cfg.llm_routing_latency_weight,
    }


def _provider_allows_sensitivity(
    provider: str, sensitivity: Sensitivity, cfg: Settings
) -> tuple[bool, str]:
    """Decide se um provider pode receber dado com esta sensibilidade.

    Retorna (permitido, motivo). O motivo vai para o audit log e para o
    endpoint /llm/policy - e a resposta objetiva a "por que isso foi
    (nao) permitido?".

    Fail-closed em todas as branches desconhecidas: so
    'cloud_with_dlp' EXPLICITO libera cloud. Um Settings invalido
    montado sem passar pelo pydantic (model_copy em teste, por exemplo)
    nega em vez de liberar.
    """
    origin = resolve_provider_origin(provider, cfg)
    if not origin:
        return False, f"origin nao resolvida para o provider '{provider}'"

    is_local = is_loopback_origin(origin)

    if sensitivity == "public":
        return True, f"dado public: origem {origin} nao e restringida"

    # daqui para frente: confidential
    if is_local:
        return True, f"origem local {origin} sob jurisdicao do operador"

    if cfg.data_sovereignty_mode != "cloud_with_dlp":
        return (
            False,
            (
                f"dado confidencial para cloud exige data_sovereignty_mode="
                f"'cloud_with_dlp' (atual: '{cfg.data_sovereignty_mode or '(vazio)'}')"
            ),
        )

    # Parseia CONFIDENTIAL_ALLOWED_ORIGINS
    raw = (cfg.confidential_allowed_origins or "").strip()
    if not raw:
        return (
            False,
            (
                f"dado confidencial para cloud exige a origem na allowlist "
                f"CONFIDENTIAL_ALLOWED_ORIGINS (vazia; destino e {origin})"
            ),
        )

    allowed: set[str] = set()
    for item in raw.split(","):
        origin_allowed = normalize_origin(item)
        if origin_allowed:
            allowed.add(origin_allowed)

    if origin in allowed:
        return True, f"origem {origin} esta na allowlist de dado confidencial"
    return (
        False,
        (f"origem {origin} nao esta em CONFIDENTIAL_ALLOWED_ORIGINS ({sorted(allowed)})"),
    )


def _select_allowed_providers(
    sensitivity: Sensitivity, primary: str, fallback: str | None, cfg: Settings
) -> list[str]:
    """DA-39 + DA-43: filtra os candidatos pela policy de soberania."""
    candidates = [primary] + ([fallback] if fallback else [])
    # remove duplicatas preservando ordem (primario tem prioridade)
    seen: set[str] = set()
    candidates = [p for p in candidates if not (p in seen or seen.add(p))]

    allowed: list[str] = []
    for provider in candidates:
        ok, reason = _provider_allows_sensitivity(provider, sensitivity, cfg)
        if ok:
            allowed.append(provider)
        else:
            logger.info(
                "AI Gateway policy: provider=%s origin=%s sensitivity=%s DENY - %s",
                provider,
                resolve_provider_origin(provider, cfg),
                sensitivity,
                reason,
            )
    return allowed


def _circuit_is_open(provider: str, cfg: Settings) -> bool:
    """Verifica se o circuito do provider esta aberto."""
    from app.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker(namespace="llm")
    return cb.is_open(provider, cfg.llm_gateway_circuit_cooldown_seconds)


def _model_id_for(provider: str, model_name: str | None, cfg: Settings) -> str | None:
    """Nome do modelo que o provider vai chamar - o mesmo que get_chat_model
    usa (app/llm/factory.py): Azure endereca por deployment; os demais por
    `model_name` (override do caller) ou `llm_model`."""
    if provider == "azure_openai":
        return cfg.azure_openai_deployment or None
    return model_name or cfg.llm_model or None


def _registry_prices_for(
    provider: str, origin: str, model_name: str | None, cfg: Settings
) -> tuple[float | None, float | None] | None:
    """Preco registrado do modelo que o provider vai de fato chamar.

    Antes (bug corrigido em 2026-10-18) a consulta usava o ROTULO do
    provider ("openai") como model_id; nenhum registro casa com isso, e a
    estimativa caia sempre na tabela interna (para gpt-4o-mini, ~33x o
    preco real), barrando candidatos por orcamento sem motivo.

    Mesma regra de resolucao do modo gerenciado (app/admin/runtime.py,
    DA-46): primeiro o modelo exato da origem; no modo gerenciado, sem ele,
    o default habilitado da origem - que e o modelo que vai responder."""
    from app.admin.repository import registry_default_prices, registry_prices

    model_id = _model_id_for(provider, model_name, cfg)
    prices = registry_prices(origin, model_id) if model_id else None
    if prices is None and cfg.llm_registry_db:
        prices = registry_default_prices(origin)
    return prices


def _estimate_cost_usd(
    provider: str, context_tokens: int, cfg: Settings, model_name: str | None = None
) -> float:
    """Estima custo em USD para a chamada usando preços reais do registry.

    Usa o preço registrado do modelo efetivo do provider (ver
    `_registry_prices_for`). Fallback para tabela interna se não houver.

    Preços por 1M tokens (in/out) são convertidos para custo por token."""

    # Tabela interna como fallback
    _PRICE_PER_1K_TOKENS_USD: dict[str, float] = {
        "ollama": 0.0,
        "openai": 0.01,
        "azure_openai": 0.01,
    }

    # Multiplicador de tokens de completion em relacao ao prompt
    _COMPLETION_TOKEN_RATIO = 0.5

    completion_tokens = int(context_tokens * _COMPLETION_TOKEN_RATIO)
    total_tokens = context_tokens + completion_tokens

    # Tenta obter preços reais do registry
    origin = resolve_provider_origin(provider, cfg)
    if origin:
        prices = _registry_prices_for(provider, origin, model_name, cfg)
        if prices and prices[0] is not None and prices[1] is not None:
            # Preços são por 1M tokens, converter para custo total
            price_in_per_token = prices[0] / 1_000_000.0
            price_out_per_token = prices[1] / 1_000_000.0
            return context_tokens * price_in_per_token + completion_tokens * price_out_per_token

    # Fallback para tabela interna (USD por 1K tokens)
    price = _PRICE_PER_1K_TOKENS_USD.get(provider, 0.0)
    return (total_tokens / 1000.0) * price


def _capabilities_match(origin: str, required: ProviderCapabilities) -> tuple[bool, str]:
    """Verifica se o provider tem as capacidades exigidas."""
    caps = capabilities_for_origin(origin)

    missing: list[str] = []
    if required.supports_seed and not caps.supports_seed:
        missing.append("seed")
    if required.supports_response_format and not caps.supports_response_format:
        missing.append("response_format")
    if required.supports_tools and not caps.supports_tools:
        missing.append("tools")

    if missing:
        return False, f"capacidades faltando: {', '.join(missing)}"
    return True, "todas as capacidades exigidas estao presentes"


def evaluate_routing(request: RoutingRequest) -> RoutingDecision:
    """Avalia o roteamento e devolve RoutingDecision.

    Fase 1: Elegibilidade rigida (soberania/origin, capability, circuito, orçamento)
    Fase 2: Scoring de preferencias (custo, latencia)

    Retorna RoutingDecision com candidatos ordenados e rejeitados com motivo.
    """
    if request.registry is not None:
        return _evaluate_registry(request)

    cfg = request.config
    sensitivity = request.sensitivity

    # Seleciona providers permitidos pela policy de soberania
    allowed_by_sovereignty = _select_allowed_providers(
        sensitivity, request.primary_provider, request.fallback_provider, cfg
    )

    candidates: list[Candidate] = []
    rejection_summary: dict[str, list[RejectionReason]] = {}

    for provider in allowed_by_sovereignty:
        origin = resolve_provider_origin(provider, cfg)
        locality = "local" if is_loopback_origin(origin) else "cloud"

        candidate_rejections: list[RejectionReason] = []

        # Fase 1: Elegibilidade rigida

        # 1.1 Circuit breaker
        if _circuit_is_open(provider, cfg):
            candidate_rejections.append(RejectionReason.CIRCUIT_OPEN)
            logger.warning(
                "AI Gateway routing: provider=%s status=circuit_open - rejeitado",
                provider,
            )

        # 1.2 Orçamento
        estimated_cost = _estimate_cost_usd(
            provider, request.context_tokens, cfg, model_name=request.model_name
        )
        if estimated_cost > request.budget_remaining_usd:
            candidate_rejections.append(RejectionReason.BUDGET_EXCEEDED)
            logger.warning(
                "AI Gateway routing: provider=%s estimated_cost=%.4f budget=%.4f - rejeitado",
                provider,
                estimated_cost,
                request.budget_remaining_usd,
            )

        # 1.3 Capacidades exigidas
        caps_ok, caps_reason = _capabilities_match(origin, request.required_capabilities)
        if not caps_ok:
            candidate_rejections.append(RejectionReason.CAPABILITY_MISSING)
            logger.warning(
                "AI Gateway routing: provider=%s reason=%s - rejeitado",
                provider,
                caps_reason,
            )

        # Registra rejeicoes
        if candidate_rejections:
            rejection_summary[provider] = candidate_rejections
            candidates.append(
                Candidate(
                    provider=provider,
                    origin=origin,
                    locality=locality,
                    allowed=False,
                    rejection_reasons=candidate_rejections,
                    estimated_cost_usd=estimated_cost,
                    latency_ms=None,  # Sem dados historicos para rejeitados
                )
            )
        else:
            # Provider elegivel - scoring de preferencias (custo)
            candidates.append(
                Candidate(
                    provider=provider,
                    origin=origin,
                    locality=locality,
                    allowed=True,
                    rejection_reasons=[],
                    estimated_cost_usd=estimated_cost,
                    latency_ms=None,  # Sem dados historicos para elegiveis
                )
            )

    # Ordena candidatos por custo (menor primeiro) como scoring de preferencias
    candidates.sort(key=lambda c: c.estimated_cost_usd)

    # Seleciona o melhor provider
    allowed_candidates = [c for c in candidates if c.allowed]
    selected_provider = allowed_candidates[0].provider if allowed_candidates else None
    selected_reason = None
    if selected_provider:
        selected_c = next(c for c in candidates if c.provider == selected_provider)
        selected_reason = f"custo_estimado=${selected_c.estimated_cost_usd:.4f}"

    all_rejected = len(allowed_candidates) == 0

    return RoutingDecision(
        request=request,
        candidates=candidates,
        selected_provider=selected_provider,
        selected_reason=selected_reason,
        all_rejected=all_rejected,
        rejection_summary={p: list(rs) for p, rs in rejection_summary.items()},
    )


# ---------------------------------------------------------------------------
# DA-72: roteamento por registro de modelos
# ---------------------------------------------------------------------------

_INTERNAL_PRICE_PER_1K = {"ollama": 0.0, "openai": 0.01, "azure_openai": 0.01}
_COMPLETION_RATIO = 0.5


def _registry_cost(model, label: str, context_tokens: int) -> float:
    completion = int(context_tokens * _COMPLETION_RATIO)
    if model.price_in_per_1m is not None and model.price_out_per_1m is not None:
        return (
            context_tokens * model.price_in_per_1m + completion * model.price_out_per_1m
        ) / 1_000_000.0
    return (context_tokens + completion) / 1000.0 * _INTERNAL_PRICE_PER_1K.get(label, 0.0)


def _effective_capabilities(origin: str, model) -> ProviderCapabilities:
    """Invariante 9 (emendada pela DA-72): a ORIGEM e' o teto; o modelo so
    restringe. True no registro nunca amplia o que a origem nao aceita."""
    base = capabilities_for_origin(origin)

    def _cap(origin_value: bool, model_value: bool | None) -> bool:
        return origin_value and (model_value is not False)

    return ProviderCapabilities(
        supports_seed=_cap(base.supports_seed, model.supports_seed),
        supports_response_format=_cap(
            base.supports_response_format, model.supports_response_format
        ),
        supports_tools=_cap(base.supports_tools, model.supports_tools),
    )


def _normalized(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi == lo:
        return {k: 0.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def _evaluate_registry(request: RoutingRequest) -> RoutingDecision:
    """Cada modelo habilitado e' um candidato. Fase 1 (rejeita com motivo
    enumerado), fase 2 (score custo x latencia, depois prioridade, depois
    default da origem). Nenhum I/O: o snapshot ja vem lido."""
    from app.llm.registry_routing import (
        candidate_settings,
        destination_matches_origin,
        effective_tier,
        effective_transport,
    )

    cfg = request.config
    snapshot = request.registry
    total_tokens = request.context_tokens + int(request.context_tokens * _COMPLETION_RATIO)

    candidates: list[Candidate] = []
    rejection_summary: dict[str, list[RejectionReason]] = {}

    for model in snapshot.models:
        label, cand_cfg = candidate_settings(model, cfg)
        transport = effective_transport(model, cfg)
        origin = resolve_provider_origin(label, cand_cfg)
        locality: Locality = "local" if is_loopback_origin(origin) else "cloud"
        tier = effective_tier(model)
        reasons: list[RejectionReason] = []
        detail: list[str] = []

        if not destination_matches_origin(model, origin):
            reasons.append(RejectionReason.DESTINATION_MISMATCH)
            detail.append(f"base_url aponta para {origin}, fora da origem {model.provider_origin}")

        ok, why = _provider_allows_sensitivity(label, request.sensitivity, cand_cfg)
        if not ok:
            reasons.append(RejectionReason.SOVEREIGNTY_BLOCKED)
            detail.append(why)

        if transport != "ollama" and model.provider_origin not in snapshot.credentialed_origins:
            reasons.append(RejectionReason.CREDENTIAL_MISSING)
            detail.append(f"origem {model.provider_origin} sem credencial cifrada (DA-47)")

        caps_ok, caps_why = _caps_satisfy(
            _effective_capabilities(origin, model), request.required_capabilities
        )
        if not caps_ok:
            reasons.append(RejectionReason.CAPABILITY_MISSING)
            detail.append(caps_why)

        if model.context_window_tokens and total_tokens > model.context_window_tokens:
            reasons.append(RejectionReason.CONTEXT_WINDOW)
            detail.append(
                f"contexto estimado {total_tokens} > janela {model.context_window_tokens}"
            )

        if model.domains and request.domain and request.domain not in model.domains:
            reasons.append(RejectionReason.DOMAIN_MISMATCH)
            detail.append(f"dominio '{request.domain}' fora de {list(model.domains)}")

        if tier == "premium":
            # Tier 3 da DA-44: cadastrado e bloqueado ate decisao propria.
            reasons.append(RejectionReason.TIER_NOT_AUTHORIZED)
            detail.append("nivel premium exige escalonamento autorizado (fora da DA-72)")

        used = snapshot.usage_tokens.get((model.provider_origin, model.model_id), 0)
        if model.monthly_limit_tokens and used >= model.monthly_limit_tokens:
            reasons.append(RejectionReason.MONTHLY_LIMIT)
            detail.append(f"{used} de {model.monthly_limit_tokens} tokens no periodo")

        if _circuit_is_open(origin, cfg):
            reasons.append(RejectionReason.CIRCUIT_OPEN)
            detail.append(f"circuito aberto para a origem {origin}")

        cost = _registry_cost(model, label, request.context_tokens)
        if cost > request.budget_remaining_usd:
            reasons.append(RejectionReason.BUDGET_EXCEEDED)
            detail.append(
                f"custo estimado ${cost:.4f} > restante ${request.budget_remaining_usd:.4f}"
            )

        key = f"{model.provider_origin}/{model.model_id}"
        if reasons:
            rejection_summary[key] = reasons
        candidates.append(
            Candidate(
                provider=label,
                origin=origin,
                locality=locality,
                allowed=not reasons,
                rejection_reasons=reasons,
                estimated_cost_usd=cost,
                latency_ms=snapshot.latency_ms.get((model.provider_origin, model.model_id)),
                model_id=model.model_id,
                registry_id=model.id,
                tier=tier,
                priority=model.priority,
                is_default=model.is_default,
                detail=tuple(detail),
            )
        )

    # Fase 2: score so entre os elegiveis
    eligible = [i for i, c in enumerate(candidates) if c.allowed]
    weights = _get_scoring_weights(cfg)
    cost_n = _normalized({i: candidates[i].estimated_cost_usd for i in eligible})
    lat_n = _normalized(
        {i: candidates[i].latency_ms for i in eligible if candidates[i].latency_ms is not None}
    )
    scored: list[Candidate] = []
    for i, c in enumerate(candidates):
        if not c.allowed:
            scored.append(c)
            continue
        score = weights["cost_weight"] * cost_n.get(i, 0.0) + weights["latency_weight"] * lat_n.get(
            i, 0.0
        )
        scored.append(dataclasses.replace(c, score=round(score, 6)))

    allowed = sorted(
        (c for c in scored if c.allowed),
        key=lambda c: (c.score, c.priority, not c.is_default, c.estimated_cost_usd, c.model_id),
    )
    rejected = [c for c in scored if not c.allowed]
    ordered = allowed + rejected

    selected = allowed[0] if allowed else None
    return RoutingDecision(
        request=request,
        candidates=ordered,
        selected_provider=selected.provider if selected else None,
        selected_reason=(
            f"modelo={selected.model_id} score={selected.score} "
            f"custo_estimado=${selected.estimated_cost_usd:.4f}"
            if selected
            else None
        ),
        all_rejected=selected is None,
        rejection_summary=rejection_summary,
    )


def _caps_satisfy(caps: ProviderCapabilities, required: ProviderCapabilities) -> tuple[bool, str]:
    missing = [
        name
        for name, need, have in (
            ("seed", required.supports_seed, caps.supports_seed),
            ("response_format", required.supports_response_format, caps.supports_response_format),
            ("tools", required.supports_tools, caps.supports_tools),
        )
        if need and not have
    ]
    if missing:
        return False, f"capacidades faltando: {', '.join(missing)}"
    return True, "todas as capacidades exigidas estao presentes"


def describe_effective_policy(cfg: Settings | None = None) -> dict:
    """DA-43: snapshot LEGIVEL da policy de soberania em vigor.

    Existe para responder a pergunta de um questionario de seguranca de
    cliente - "para onde vai meu dado confidencial?" - sem nenhum
    'confia'. Para cada provider conhecido mostra a ORIGIN real
    resolvida, a localidade, e se pode receber 'public' e 'confidential',
    sempre com o motivo da decisao.

    NUNCA inclui chave, token ou o conteudo de openai_api_key /
    azure_openai_api_key - so origens, que nao carregam credencial
    (normalize_origin descarta userinfo).
    """
    conf = cfg or settings
    known = ["ollama", "openai", "azure_openai"]
    for extra in (conf.llm_provider, conf.llm_fallback_provider):
        if extra and extra not in known:
            known.append(extra)

    providers: dict[str, dict] = {}
    for name in known:
        origin = resolve_provider_origin(name, conf)
        pub_ok, pub_reason = _provider_allows_sensitivity(name, "public", conf)
        conf_ok, conf_reason = _provider_allows_sensitivity(name, "confidential", conf)
        providers[name] = {
            "locality": "local" if is_loopback_origin(origin) else "cloud",
            "origin": origin or None,
            "may_receive_public": pub_ok,
            "may_receive_confidential": conf_ok,
            "public_reason": pub_reason,
            "confidential_reason": conf_reason,
        }

    # Determina quais providers seriam roteados para dados confidenciais ou publicos
    # Usa a mesma logica de _select_allowed_providers para garantir consistencia
    allowed_for_confidential = _select_allowed_providers(
        "confidential", conf.llm_provider, conf.llm_fallback_provider, conf
    )
    allowed_for_public = _select_allowed_providers(
        "public", conf.llm_provider, conf.llm_fallback_provider, conf
    )

    return {
        "data_sovereignty_mode": conf.data_sovereignty_mode,
        "sensitivity_default": conf.sensitivity_default,
        "confidential_allowed_origins": sorted(
            normalize_origin(item)
            for item in (conf.confidential_allowed_origins or "").split(",")
            if normalize_origin(item)
        ),
        "routing": {
            "primary": conf.llm_provider,
            "fallback": conf.llm_fallback_provider or None,
            "allowed_for_confidential": allowed_for_confidential,
            "allowed_for_public": allowed_for_public,
        },
        "limits": {
            "max_cost_usd": conf.llm_gateway_max_cost_usd,
            "request_timeout_seconds": conf.llm_request_timeout_seconds,
        },
        "providers": providers,
        "note": (
            "A policy e avaliada por ORIGIN (scheme://host[:port]), nao pelo "
            "rotulo do provider: 'openai' pode apontar para api.openai.com ou "
            "para qualquer endpoint compativel via OPENAI_BASE_URL. Dado "
            "confidential so sai se a origin estiver em "
            "confidential_allowed_origins E data_sovereignty_mode="
            "'cloud_with_dlp'. Sem isso, so providers locais. Fail-closed."
        ),
    }
