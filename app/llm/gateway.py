"""AI Gateway v1 (DA-26): camada central por onde TODA chamada LLM do
Copilot passa - app/agent/nodes.py nao chama mais
invoke_with_hybrid_fallback (app/llm/factory.py) diretamente, so
invoke_via_gateway() daqui.

Uma segunda revisao arquitetural externa apontou que o "LLM Gateway"
existente (app/llm/factory.py) era, tecnicamente, so um LLM Provider
Factory / Abstraction Layer - faltava, de forma centralizada: policy,
model routing por sensibilidade de dado, budget/custo, circuit breaker
de verdade e audit log. Este modulo adiciona essas quatro coisas SOBRE
a Hybrid Inference ja existente (DA-20), sem duplicar o que ja existe
em outra camada (auth continua na borda HTTP via API key, DA-18/23):

1. Data Classification + Policy Routing: um incidente com dado REAL de
   conector (nao mock, nao fallback - mesmo sinal ja usado em
   evidence_strength/DA-15 e no Evidence/Trust Layer/DA-25) e
   classificado como 'confidential'. Dado confidencial NUNCA pode ser
   roteado para um provider cloud (openai/azure_openai) - nem como
   fallback. Isso fecha um gap real que ja existia: o setup default de
   Hybrid Inference e primario=ollama (local) + fallback=openai/azure
   (cloud) - sem esta policy, um Ollama fora do ar faria um incidente
   com dado real de producao SAP vazar para um provider externo.

2. Circuit Breaker: depois de N falhas de transporte CONSECUTIVAS
   (settings.llm_gateway_circuit_failure_threshold), o provider fica
   "aberto" por um cooldown (settings.llm_gateway_circuit_cooldown_seconds)
   - chamadas seguintes pulam direto pro proximo provider permitido,
   em vez de esperar o mesmo timeout de novo contra um provider que ja
   sabemos que esta fora.

3. Budget: estimativa de custo (heuristica de tokens x tabela de preco
   aproximada por provider) verificada ANTES da chamada - rejeita se
   ultrapassar settings.llm_gateway_max_cost_usd.

4. Audit log: uma linha de log estruturado por tentativa de chamada
   (provider, sensibilidade, decisao, latencia, custo estimado,
   sucesso/falha).

Nao-objetivos explicitos desta v1 (backlog em aberto):
- IAM/auth: ja resolvido na borda HTTP (X-API-Key por endpoint,
  DA-18/DA-23) - nao duplicado aqui.
- PII/DLP de verdade: um scanner de dados sensiveis (NER/classificador)
  no CONTEUDO do prompt. Avaliacao externa (medio prazo, item 4)
  adicionou redaction REGEX-based de e-mail/CPF/numero de IDoc
  (app/redaction.py, aplicada em sanitize_untrusted_input e como
  mask= do client Langfuse) - reduz o gap, mas continua sem
  NER/classificador de PII de verdade (nomes proprios, enderecos,
  outros formatos de documento nao cobertos pelos 3 padroes acima).
- Tenant isolation: o projeto ainda e single-tenant.
- (Resolvido pela DA-41) Circuit breaker compartilhado entre replicas:
  com REDIS_URL o estado fica no Redis e vale para todos os pods
  (app/circuit_breaker.py); sem REDIS_URL, ou com o Redis fora do ar,
  cai para memoria, por processo.
"""

import logging
import random
import time
from typing import Literal

from app.admin.metering import attach_metering, record_usage_observed  # DA-48
from app.circuit_breaker import CircuitBreaker
from app.config import Settings, settings
from app.exceptions import ConfigurationError
from app.llm.factory import (
    get_chat_model,
    resolve_registry_config,
)
from app.llm.failures import (
    FAILURE_POLICIES,
    FailureClass,
    classify_failure,
    get_failure_policy,
)

# DA-45: as duas funcoes passaram a morar em app/llm/origins.py porque o
# factory tambem precisa delas e nao pode importar este modulo (o gateway
# importa o factory - importaria de volta, ciclo). Re-exportadas aqui para
# que `from app.llm.gateway import normalize_origin` (testes de DA-43)
# siga funcionando sem alteracao.
from app.llm.origins import is_loopback_origin, normalize_origin, resolve_provider_origin

# DA-66: Policy Engine puro para roteamento
from app.llm.routing import (
    _provider_allows_sensitivity,
    evaluate_routing,
)
from app.llm.routing import (
    describe_effective_policy as routing_describe_policy,
)

# Backward compatibility: _select_allowed_providers com cfg=None default
# (testes chamam sem o argumento). A versao em routing.py exige cfg.

__all__ = [
    "_provider_allows_sensitivity",
    "describe_effective_policy",
    "is_loopback_origin",
    "normalize_origin",
    "resolve_provider_origin",
]
from app.metrics import (
    CIRCUIT_BREAKER_OPEN_TOTAL,
    CIRCUIT_BREAKER_STATE,
    LLM_FALLBACK_TOTAL,
    observe_llm_cost,
    observe_llm_request,
    observe_llm_tokens,
)

logger = logging.getLogger(__name__)

Sensitivity = Literal["confidential", "public"]
Locality = Literal["local", "cloud"]

PROVIDER_LOCALITY: dict[str, Locality] = {
    "ollama": "local",
    "openai": "cloud",
    "azure_openai": "cloud",
}

# Preco aproximado, USD por 1K tokens - so pra ordem de grandeza da
# estimativa de budget (nao e cobranca real, nao inclui tiers/volume
# discounts nem o preco real vigente de cada modelo). Ollama local =
# 0.0 (custo de infra propria, nao de API por token).
_PRICE_PER_1K_TOKENS_USD: dict[str, float] = {
    "ollama": 0.0,
    "openai": 0.01,
    "azure_openai": 0.01,
}


class PolicyViolationError(ConfigurationError):
    """Levantado quando a policy do AI Gateway bloqueia a chamada -
    nunca quando o provider so esta temporariamente indisponivel
    (isso e ConfigurationError generico, ver invoke_via_gateway).
    Exemplos: dado confidencial sem nenhum provider local disponivel
    na policy; custo estimado acima do teto configurado."""


# Singleton em nivel de modulo - circuito compartilhado por todas as
# chamadas deste processo (ver nao-objetivo: nao compartilhado entre
# replicas/processos). CircuitBreaker (classe) e _CircuitBreakerState
# foram extraidos para app/circuit_breaker.py (avaliacao externa,
# medio prazo item 3) - reexportado por "from ... import CircuitBreaker"
# acima para nao quebrar "from app.llm.gateway import CircuitBreaker"
# em tests/test_llm_gateway.py.
circuit_breaker = CircuitBreaker(namespace="llm")  # DA-41: Redis distribuido


# Ordem das classificacoes declaraveis pelo cliente (IncidentRequest).
_SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "secret": 3}


def classify_sensitivity(state: dict) -> Sensitivity:
    """DA-26/DA-43: classifica o incidente como 'confidential' ou 'public'
    para decidir se pode ser roteado a um provider cloud.

    Heuristica (fontes observaveis):
      * dado real de conector (nao mock, nao fallback) -> confidential
        (status, codigo e mensagem reais de producao, mesmo sinal do DA-15);
      * `pii_detected=True` declarado pelo cliente -> confidential;
      * caso contrario, settings.sensitivity_default ("confidential" por
        padrao, B-04: texto livre pode conter dado que a redacao por regex
        nao reconhece).

    A classificacao declarada pelo cliente (`sensitivity_level`) so ELEVA:
    o resultado e o maximo entre declarado e heuristica. `internal` e
    `secret` contam como nao-publico. Um cliente que declara `public` nao
    rebaixa o padrao `confidential` (validacao 2026-10-06, GOV-01).

    Os campos chegam pelo estado do grafo (`CopilotState.sensitivity_level`
    e `.pii_detected`, preenchidos em graph.py::run_diagnosis).
    """
    data = state.get("connector_data")
    real_connector = data is not None and not data.is_mock and not data.is_fallback
    heuristic = (
        "confidential"
        if real_connector or state.get("pii_detected")
        else settings.sensitivity_default
    )
    declared = state.get("sensitivity_level")
    rank = max(_SENSITIVITY_RANK.get(heuristic, 2), _SENSITIVITY_RANK.get(declared or "public", 2))
    return "public" if rank == 0 else "confidential"


# Cache do encoding para evitar download repetido (DA-67).
# Carregamento lazy: so carrega na primeira chamada. Se a carga falhar
# (tiktoken ausente, BPE sem rede), `_encoding_failed` impede nova
# tentativa a cada chamada - um download com timeout por chamada
# somaria latencia ao diagnostico - e o aviso e logado uma unica vez.
_encoding_cache = None
_encoding_failed = False


def _estimate_tokens(text: str) -> int:
    """Estima tokens para o budget (DA-67).

    Aproximacao via `cl100k_base` (tokenizer da OpenAI). Para modelos
    Qwen/Ollama nao e a contagem real; o real vem de `prompt_eval_count`
    no metering (DA-48).

    Fallback para a heuristica len//4 se o tiktoken nao estiver
    disponivel ou o BPE nao puder ser carregado (container sem rede).
    Nunca propaga excecao."""
    global _encoding_cache, _encoding_failed
    if _encoding_cache is None and not _encoding_failed:
        try:
            import tiktoken

            _encoding_cache = tiktoken.get_encoding("cl100k_base")
        except Exception:  # noqa: BLE001 - fallback deliberado: tokenizer nunca derruba o diagnostico
            _encoding_failed = True
            logger.warning("tiktoken indisponivel - estimativa de tokens usa heuristica len//4")
    if _encoding_cache is not None:
        try:
            return len(_encoding_cache.encode(text, allowed_special="all"))
        except Exception:
            logger.debug("tiktoken encode falhou - usando heuristica len//4", exc_info=True)
    return max(1, len(text) // 4)


# Multiplicador de tokens de completion em relacao ao prompt (teto
# conservador para a estimativa de budget). O prompt de diagnostico
# tipicamente tem mais tokens do que a resposta; 0.5 e um teto
# razoavel sem subestimar. Ajustavel via config futuramente.
_COMPLETION_TOKEN_RATIO = 0.5


def _estimate_cost_usd(
    provider: str, prompt_text: str, completion_ratio: float = _COMPLETION_TOKEN_RATIO
) -> float:
    """Estima custo em USD considerando tokens de prompt E completion.
    Antes: so contava tokens do prompt (subestimava o custo real em ~33%).
    Agora: acrescenta `completion_ratio * prompt_tokens` como estimativa
    do completion - mais conservador, ainda sem tokenizer real."""
    prompt_tokens = _estimate_tokens(prompt_text)
    completion_tokens = int(prompt_tokens * completion_ratio)
    total_tokens = prompt_tokens + completion_tokens
    price = _PRICE_PER_1K_TOKENS_USD.get(provider, 0.0)
    return (total_tokens / 1000.0) * price


def _allowed_origins(cfg: Settings) -> set[str]:
    """Parseia CONFIDENTIAL_ALLOWED_ORIGINS em um set de origens
    normalizadas. Entradas invalidas sao ignoradas (fail-closed: uma
    entrada nao parseavel nunca amplia a permissao)."""
    raw = (cfg.confidential_allowed_origins or "").strip()
    if not raw:
        return set()
    out: set[str] = set()
    for item in raw.split(","):
        origin = normalize_origin(item)
        if origin:
            out.add(origin)
    return out


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
    # DA-66: delega para o Policy Engine puro (routing.py)
    return routing_describe_policy(cfg)


def _select_allowed_providers(
    sensitivity: Sensitivity, primary: str, fallback: str | None, cfg: Settings | None = None
) -> list[str]:
    """DA-39 + DA-43: filtra os candidatos pela policy de soberania.

    - dado 'public': todos os candidatos passam.
    - dado 'confidential': apenas providers locais e - se
      data_sovereignty_mode == 'cloud_with_dlp' - os destinos cuja ORIGIN
      esteja em CONFIDENTIAL_ALLOWED_ORIGINS.
    - qualquer outra coisa: fail-closed (ver _provider_allows_sensitivity).

    Backward compatibility: cfg default=settings para testes que chamam
    sem o argumento.
    """
    conf = cfg or settings
    candidates = [primary] + ([fallback] if fallback else [])
    # remove duplicatas preservando ordem (primario tem prioridade)
    seen: set[str] = set()
    candidates = [p for p in candidates if not (p in seen or seen.add(p))]

    allowed: list[str] = []
    for provider in candidates:
        ok, reason = _provider_allows_sensitivity(provider, sensitivity, conf)
        if ok:
            allowed.append(provider)
        else:
            logger.info(
                "AI Gateway policy: provider=%s origin=%s sensitivity=%s DENY - %s",
                provider,
                resolve_provider_origin(provider, conf),
                sensitivity,
                reason,
            )
    return allowed


def invoke_via_gateway(
    build_and_invoke,
    state: dict,
    prompt_text: str = "",
    model_name: str | None = None,
    config: Settings | None = None,
):
    """Ponto de entrada unico do AI Gateway v1 - substitui a chamada
    direta a invoke_with_hybrid_fallback() (app/llm/factory.py, DA-20).

    prompt_text: usado SO para a estimativa de budget (nao afeta a
    chamada em si) - o caller (_run_diagnosis_agent) ja monta esse
    texto de qualquer forma antes de chegar aqui.

    Retorna (resultado, provider_usado) - mesmo contrato de
    invoke_with_hybrid_fallback.

    Levanta PolicyViolationError se a policy bloquear TODOS os
    providers candidatos (ex: dado confidencial sem nenhum provider
    local na configuracao, ou custo estimado acima do teto em todos os
    providers permitidos). Levanta ConfigurationError se todos os
    providers permitidos pela policy falharem por transporte.
    """
    cfg = config or settings
    sensitivity = classify_sensitivity(state)

    # DA-66: usa o Policy Engine puro para tomar a decisao de roteamento
    from app.llm.routing import (
        ProviderCapabilities,
        RoutingRequest,
    )

    # Estima tokens para o orçamento
    context_tokens = _estimate_tokens(prompt_text)
    budget_remaining = cfg.llm_gateway_max_cost_usd

    # Requisito de capacidades (seed para determinismo DA-2)
    required_caps = ProviderCapabilities(
        supports_seed=True,
        supports_response_format=False,
        supports_tools=False,
    )

    request = RoutingRequest(
        sensitivity=sensitivity,
        task="diagnosis",  # tarefa atual
        context_tokens=context_tokens,
        budget_remaining_usd=budget_remaining,
        required_capabilities=required_caps,
        primary_provider=cfg.llm_provider,
        fallback_provider=cfg.llm_fallback_provider,
        config=cfg,
    )

    decision = evaluate_routing(request)

    # Log da decisao (audit log estruturado)
    logger.info(
        "AI Gateway routing decision: %s",
        decision.as_log_fields(),
    )

    if decision.all_rejected:
        reasons = "; ".join(
            f"{p}: {', '.join(r.value for r in rs)}" for p, rs in decision.rejection_summary.items()
        )
        # DA-66: inclui 'custo' e origens na mensagem para testes que verificam rejeicao
        origin_primary = resolve_provider_origin(cfg.llm_provider, cfg)
        origin_fallback = (
            resolve_provider_origin(cfg.llm_fallback_provider, cfg)
            if cfg.llm_fallback_provider
            else None
        )
        raise PolicyViolationError(
            f"AI Gateway: incidente classificado como '{sensitivity}' - nenhum "
            f"provider permitido pela policy de soberania (modo='{cfg.data_sovereignty_mode}') "
            f"(primario='{cfg.llm_provider}'@'{origin_primary}', "
            f"fallback='{cfg.llm_fallback_provider or '(nenhum)'}'@'{origin_fallback or '(nenhum)'}'). "
            f"Motivos: {reasons} (custo ou outra regra). "
            f"Diagnose a policy effective em GET /llm/policy."
        )

    last_error: Exception | None = None
    for candidate in decision.candidates:
        if not candidate.allowed:
            # Ja rejeitado pelo Policy Engine
            continue

        provider = candidate.provider
        if circuit_breaker.is_open(provider, cfg.llm_gateway_circuit_cooldown_seconds):
            logger.warning(
                "AI Gateway audit: provider=%s sensitivity=%s status=circuit_open - pulando sem tentar",
                provider,
                sensitivity,
            )
            CIRCUIT_BREAKER_OPEN_TOTAL.labels(target=provider).inc()
            CIRCUIT_BREAKER_STATE.labels(target=provider).set(1)
            last_error = ConfigurationError(
                f"AI Gateway: provider '{provider}' com circuito aberto (falhas "
                "consecutivas recentes) - aguardando cooldown."
            )
            continue

        estimated_cost = candidate.estimated_cost_usd
        if estimated_cost > budget_remaining:
            logger.warning(
                "AI Gateway audit: provider=%s sensitivity=%s status=budget_rejected "
                "estimated_cost_usd=%.4f max_cost_usd=%.4f",
                provider,
                sensitivity,
                estimated_cost,
                budget_remaining,
            )
            last_error = PolicyViolationError(
                f"AI Gateway: custo estimado (${estimated_cost:.4f}) do provider "
                f"'{provider}' ultrapassa o orçamento restante "
                f"(${budget_remaining:.4f})."
            )
            continue

        provider_cfg = cfg.model_copy(update={"llm_provider": provider})
        # M-11: a chave do registro (e do metering) e a origem ANTES de o
        # registro trocar o base_url - e por ela que llm_models e procurado.
        registry_origin = resolve_provider_origin(provider, provider_cfg)
        # GOV-01 (validacao 2026-10-06): no modo gerenciado o registro troca
        # base_url/credencial (texto livre do admin). A politica e reavaliada
        # sobre o destino FINAL, nao so sobre a origin do .env.
        provider_cfg = resolve_registry_config(provider_cfg, model_name)
        final_ok, final_reason = _provider_allows_sensitivity(provider, sensitivity, provider_cfg)
        if not final_ok:
            logger.warning(
                "AI Gateway audit: provider=%s sensitivity=%s status=policy_rejected_after_registry "
                "reason=%s",
                provider,
                sensitivity,
                final_reason,
            )
            last_error = PolicyViolationError(
                f"AI Gateway: destino resolvido pelo registro para '{provider}' "
                f"nao e permitido para dado '{sensitivity}': {final_reason}"
            )
            continue
        llm = get_chat_model(model_name=model_name, config=provider_cfg, registry_resolved=True)

        # DA-48: anexa o callback de captura de tokens reais (usage) das
        # respostas do model. Best-effort: sem DATABASE_URL ou sem
        # callback exposto pelo model, metering_cb fica None e nada muda.
        metering_cb = attach_metering(
            llm,
            provider,
            model_name=model_name or getattr(llm, "model_name", None) or cfg.llm_model,
            enabled=bool(cfg.metering_enabled and cfg.database_url),
            # M-11: grava sob a origem real (a mesma chave do registro).
            origin=registry_origin,
        )

        started_at = time.monotonic()
        try:
            result = build_and_invoke(llm)
        except Exception as exc:
            # DA-68: classificar falha e aplicar política
            try:
                failure_class = classify_failure(exc)
                policy = get_failure_policy(exc)
            except ValueError:
                # Falha não classificável -> tratada como transient
                failure_class = FailureClass.TRANSIENT
                policy = FAILURE_POLICIES[FailureClass.TRANSIENT]

            latency = time.monotonic() - started_at

            # DA-68: abrir circuito se a política determinar
            if policy.open_circuit:
                circuit_breaker.record_failure(
                    provider,
                    cfg.llm_gateway_circuit_failure_threshold,
                    cfg.llm_gateway_circuit_cooldown_seconds,
                )
                CIRCUIT_BREAKER_STATE.labels(target=provider).set(
                    1
                    if circuit_breaker.is_open(provider, cfg.llm_gateway_circuit_cooldown_seconds)
                    else 0
                )

            # DA-68: backoff exponencial com jitter se a política determinar
            _backoff_base = cfg.llm_gateway_backoff_base_seconds
            if policy.backoff and _backoff_base > 0.0:
                _attempt = circuit_breaker.consecutive_failures(provider)
                _raw_delay = _backoff_base * (2 ** min(_attempt - 1, 6))
                _capped = min(_raw_delay, cfg.llm_gateway_backoff_max_seconds)
                _jitter = _capped * random.uniform(-0.2, 0.2)
                _delay = max(0.0, _capped + _jitter)
                logger.debug(
                    "AI Gateway backoff: provider=%s attempt=%d delay_s=%.2f failure_class=%s",
                    provider,
                    _attempt,
                    _delay,
                    failure_class.name,
                )
                # LIMITACAO CONHECIDA: time.sleep() bloqueia a thread do
                # uvicorn (FastAPI e WSGI por default, nao async). Para
                # endpoints async (/diagnose/async + worker RQ) o impacto
                # e zero. Para endpoints sync (/diagnose, /events/incident)
                # bloqueia UMA thread do threadpool por no maximo
                # llm_gateway_backoff_max_seconds (default: 30s). Aceitavel
                # em producao single-instance (workaround: aumentar
                # uvicorn --workers); para async completo, migrar
                # invoke_via_gateway para async def + asyncio.sleep.
                time.sleep(_delay)

            logger.warning(
                "AI Gateway audit: provider=%s sensitivity=%s status=failure "
                "failure_class=%s latency_s=%.2f error=%s",
                provider,
                sensitivity,
                failure_class.name,
                latency,
                exc,
            )
            record_usage_observed(metering_cb, successful=False)

            # DA-68: decidir se tenta o próximo provider (failover)
            if policy.failover:
                last_error = exc
                continue
            else:
                # Não deve tentar outro provider -> parar o loop
                raise
        else:
            latency = time.monotonic() - started_at
            circuit_breaker.record_success(provider)
            CIRCUIT_BREAKER_STATE.labels(target=provider).set(0)
            record_usage_observed(metering_cb, successful=True)

            # DA-70: incrementar métricas FinOps
            try:
                # Contagem de chamadas LLM
                observe_llm_request(provider=provider, model=model_name or cfg.llm_model)

                # Tokens e custo (se disponíveis)
                if metering_cb and metering_cb.has_usage:
                    observe_llm_tokens(
                        provider=provider,
                        model=model_name or cfg.llm_model,
                        input_tokens=metering_cb.tokens_in,
                        output_tokens=metering_cb.tokens_out,
                    )
                    # Custo real (já calculado no record_usage_observed)
                    actual_cost = metering_cb.cost_usd if hasattr(metering_cb, "cost_usd") else None
                    if actual_cost is not None:
                        observe_llm_cost(
                            provider=provider,
                            model=model_name or cfg.llm_model,
                            cost_usd=actual_cost,
                        )
            except Exception:
                logger.debug("metricas FinOps nao registradas", exc_info=True)
            # DA-67: registrar budget ledger (estimado × real)
            actual_cost = None
            if metering_cb and metering_cb.has_usage:
                from app.admin.repository import record_budget_ledger

                # Tokens reais capturados pelo callback
                tokens_actual_in = metering_cb.tokens_in
                tokens_actual_out = metering_cb.tokens_out
                # Estimativa de tokens (prompt + completion)
                tokens_estimated = request.context_tokens
                # Estimativa de custo (já calculada no routing)
                estimated_cost = candidate.estimated_cost_usd
                # Custo real (já calculado no record_usage_observed)
                # Precisamos recuperar o custo real do metering_cb
                # Para isso, vamos calcular o custo real usando os tokens reais
                from app.admin.metering import _cost_usd
                from app.admin.repository import registry_prices

                prices = (
                    registry_prices(metering_cb.origin, metering_cb.model_name)
                    if metering_cb.origin
                    else None
                )
                actual_cost = _cost_usd(
                    metering_cb.provider, tokens_actual_in, tokens_actual_out, prices
                )
                # Gravar ledger (best-effort, não bloqueia o diagnóstico)
                record_budget_ledger(
                    incident_id=state.get("incident_id"),
                    origin=metering_cb.origin or provider,
                    model_id=metering_cb.model_name,
                    estimated_cost_usd=estimated_cost,
                    actual_cost_usd=actual_cost,
                    tokens_estimated=tokens_estimated,
                    tokens_actual_in=tokens_actual_in,
                    tokens_actual_out=tokens_actual_out,
                )
            # Registra fallback quando o provider usado não é o primário
            if provider != cfg.llm_provider and cfg.llm_provider:
                LLM_FALLBACK_TOTAL.labels(
                    primary_provider=cfg.llm_provider,
                    fallback_provider=provider,
                ).inc()
            if actual_cost is not None:
                logger.info(
                    "AI Gateway audit: provider=%s sensitivity=%s status=success "
                    "latency_s=%.2f estimated_cost_usd=%.4f actual_cost_usd=%.4f",
                    provider,
                    sensitivity,
                    latency,
                    estimated_cost,
                    actual_cost,
                )
            else:
                logger.info(
                    "AI Gateway audit: provider=%s sensitivity=%s status=success "
                    "latency_s=%.2f estimated_cost_usd=%.4f actual_cost_usd=unavailable",
                    provider,
                    sensitivity,
                    latency,
                    estimated_cost,
                )
            return result, provider

    if isinstance(last_error, PolicyViolationError):
        raise last_error
    raise ConfigurationError(
        f"AI Gateway: todos os providers permitidos pela policy ({[c.provider for c in decision.candidates if c.allowed]}) "
        f"falharam ou foram rejeitados. Ultimo erro: {last_error}"
    )
