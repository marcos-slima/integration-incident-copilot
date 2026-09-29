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
- Circuit breaker compartilhado entre replicas: e in-memory, por
  processo - nao compartilhado entre os 2+ pods do deploy Kyma
  (DA-24). Precisaria de um backend compartilhado (Redis, por exemplo)
  para isso em producao multi-instancia.
"""

import logging
import random
import time
from typing import Literal

from app.admin.metering import attach_metering, record_usage_observed  # DA-48
from app.circuit_breaker import CircuitBreaker
from app.config import Settings, settings
from app.exceptions import ConfigurationError
from app.llm.factory import TRANSPORT_FAILURE_EXCEPTIONS, get_chat_model

# DA-45: as duas funcoes passaram a morar em app/llm/origins.py porque o
# factory tambem precisa delas e nao pode importar este modulo (o gateway
# importa o factory - importaria de volta, ciclo). Re-exportadas aqui para
# que `from app.llm.gateway import normalize_origin` (testes de DA-43)
# siga funcionando sem alteracao.
from app.llm.origins import is_loopback_origin, normalize_origin, resolve_provider_origin

__all__ = [
    "is_loopback_origin",
    "normalize_origin",
    "resolve_provider_origin",
]
from app.metrics import CIRCUIT_BREAKER_OPEN_TOTAL, CIRCUIT_BREAKER_STATE, LLM_FALLBACK_TOTAL

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


def classify_sensitivity(state: dict) -> Sensitivity:
    """DA-26: classifica o incidente como 'confidential' ou 'public'
    para decidir se pode ser roteado a um provider cloud.

    Mesmo sinal ja usado em evidence_strength (DA-15) e no Evidence/
    Trust Layer (DA-25): dado real de conector (nao mock, nao
    fallback) e informacao de sistema de producao (status, codigo de
    erro, mensagem reais) - tratado como confidential por padrao.
    Sem dado real de conector (so descricao/logs/payload enviados pelo
    usuario), vale settings.sensitivity_default - "confidential" por
    padrao (B-04): texto livre pode conter dado empresarial que a
    redacao por regex nao reconhece, entao nao classificado = sensivel.
    SENSITIVITY_DEFAULT=public restaura o comportamento anterior.
    """
    data = state.get("connector_data")
    if data is not None and not data.is_mock and not data.is_fallback:
        return "confidential"
    return settings.sensitivity_default


def _estimate_tokens(text: str) -> int:
    # Heuristica simples (~4 caracteres por token, ordem de grandeza
    # razoavel pra texto tecnico em portugues/ingles) - nao e um
    # tokenizer real, so o suficiente pra um teto de budget. Ver
    # nao-objetivo no docstring do modulo.
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

    is_local = PROVIDER_LOCALITY.get(provider) == "local"

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

    allowed = _allowed_origins(cfg)
    if not allowed:
        return (
            False,
            (
                f"dado confidencial para cloud exige a origem na allowlist "
                f"CONFIDENTIAL_ALLOWED_ORIGINS (vazia; destino e {origin})"
            ),
        )
    if origin in allowed:
        return True, f"origem {origin} esta na allowlist de dado confidencial"
    return (
        False,
        (f"origem {origin} nao esta em CONFIDENTIAL_ALLOWED_ORIGINS ({sorted(allowed)})"),
    )


def _select_allowed_providers(
    sensitivity: Sensitivity, primary: str, fallback: str, cfg: Settings | None = None
) -> list[str]:
    """DA-39 + DA-43: filtra os candidatos pela policy de soberania.

    - dado 'public': todos os candidatos passam.
    - dado 'confidential': apenas providers locais e - se
      data_sovereignty_mode == 'cloud_with_dlp' - os destinos cuja ORIGIN
      esteja em CONFIDENTIAL_ALLOWED_ORIGINS.
    - qualquer outra coisa: fail-closed (ver _provider_allows_sensitivity).
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
            "locality": PROVIDER_LOCALITY.get(name, "unknown"),
            "origin": origin or None,
            "may_receive_public": pub_ok,
            "may_receive_confidential": conf_ok,
            "public_reason": pub_reason,
            "confidential_reason": conf_reason,
            "circuit_open": circuit_breaker.is_open(
                name, conf.llm_gateway_circuit_cooldown_seconds
            ),
        }

    return {
        "data_sovereignty_mode": conf.data_sovereignty_mode,
        "sensitivity_default": conf.sensitivity_default,
        "confidential_allowed_origins": sorted(_allowed_origins(conf)),
        "routing": {
            "primary": conf.llm_provider,
            "fallback": conf.llm_fallback_provider or None,
            "allowed_for_public": _select_allowed_providers(
                "public", conf.llm_provider, conf.llm_fallback_provider, conf
            ),
            "allowed_for_confidential": _select_allowed_providers(
                "confidential", conf.llm_provider, conf.llm_fallback_provider, conf
            ),
        },
        "limits": {
            "max_cost_usd": conf.llm_gateway_max_cost_usd,
            "request_timeout_seconds": conf.llm_request_timeout_seconds,
            "circuit_failure_threshold": conf.llm_gateway_circuit_failure_threshold,
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
    allowed = _select_allowed_providers(
        sensitivity, cfg.llm_provider, cfg.llm_fallback_provider, cfg
    )

    if not allowed:
        reasons = "; ".join(
            f"{p} ({resolve_provider_origin(p, cfg) or 'origin desconhecida'}): "
            f"{_provider_allows_sensitivity(p, sensitivity, cfg)[1]}"
            for p in [cfg.llm_provider]
            + ([cfg.llm_fallback_provider] if cfg.llm_fallback_provider else [])
        )
        raise PolicyViolationError(
            f"AI Gateway: incidente classificado como '{sensitivity}' - nenhum "
            f"provider permitido pela policy de soberania "
            f"(primario='{cfg.llm_provider}', "
            f"fallback='{cfg.llm_fallback_provider or '(nenhum)'}'). "
            f"Motivos: {reasons}. Diagnose a policy effective em GET /llm/policy."
        )

    last_error: Exception | None = None
    for provider in allowed:
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

        estimated_cost = _estimate_cost_usd(provider, prompt_text)
        if estimated_cost > cfg.llm_gateway_max_cost_usd:
            logger.warning(
                "AI Gateway audit: provider=%s sensitivity=%s status=budget_rejected "
                "estimated_cost_usd=%.4f max_cost_usd=%.4f",
                provider,
                sensitivity,
                estimated_cost,
                cfg.llm_gateway_max_cost_usd,
            )
            last_error = PolicyViolationError(
                f"AI Gateway: custo estimado (${estimated_cost:.4f}) do provider "
                f"'{provider}' ultrapassa o teto configurado "
                f"(${cfg.llm_gateway_max_cost_usd:.4f})."
            )
            continue

        provider_cfg = cfg.model_copy(update={"llm_provider": provider})
        llm = get_chat_model(model_name=model_name, config=provider_cfg)

        # DA-48: anexa o callback de captura de tokens reais (usage) das
        # respostas do model. Best-effort: sem DATABASE_URL ou sem
        # callback exposto pelo model, metering_cb fica None e nada muda.
        metering_cb = attach_metering(
            llm,
            provider,
            model_name=model_name or getattr(llm, "model_name", None) or cfg.llm_model,
            enabled=bool(cfg.metering_enabled and cfg.database_url),
        )

        started_at = time.monotonic()
        try:
            result = build_and_invoke(llm)
        except TRANSPORT_FAILURE_EXCEPTIONS as exc:
            latency = time.monotonic() - started_at
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
            # DA-30 — backoff exponencial com jitter antes de tentar o
            # proximo provider. Evita bombardear um provider degradado
            # com retentativas imediatas e reduz thundering herd em
            # deploy multi-instancia (o jitter dispersa as janelas).
            # base=0.0 desabilita (ex.: testes de velocidade).
            _backoff_base = cfg.llm_gateway_backoff_base_seconds
            if _backoff_base > 0.0:
                _attempt = circuit_breaker.consecutive_failures(provider)
                _raw_delay = _backoff_base * (2 ** min(_attempt - 1, 6))
                _capped = min(_raw_delay, cfg.llm_gateway_backoff_max_seconds)
                _jitter = _capped * random.uniform(-0.2, 0.2)
                _delay = max(0.0, _capped + _jitter)
                logger.debug(
                    "AI Gateway backoff: provider=%s attempt=%d delay_s=%.2f",
                    provider,
                    _attempt,
                    _delay,
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
                "latency_s=%.2f error=%s",
                provider,
                sensitivity,
                latency,
                exc,
            )
            record_usage_observed(metering_cb, successful=False)
            last_error = exc
            continue
        else:
            latency = time.monotonic() - started_at
            circuit_breaker.record_success(provider)
            CIRCUIT_BREAKER_STATE.labels(target=provider).set(0)
            record_usage_observed(metering_cb, successful=True)
            # Registra fallback quando o provider usado não é o primário
            if provider != cfg.llm_provider and cfg.llm_provider:
                LLM_FALLBACK_TOTAL.labels(
                    primary_provider=cfg.llm_provider,
                    fallback_provider=provider,
                ).inc()
            logger.info(
                "AI Gateway audit: provider=%s sensitivity=%s status=success "
                "latency_s=%.2f estimated_cost_usd=%.4f",
                provider,
                sensitivity,
                latency,
                estimated_cost,
            )
            return result, provider

    if isinstance(last_error, PolicyViolationError):
        raise last_error
    raise ConfigurationError(
        f"AI Gateway: todos os providers permitidos pela policy ({allowed}) "
        f"falharam ou foram rejeitados. Ultimo erro: {last_error}"
    )
