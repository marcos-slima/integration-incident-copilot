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

Nao-objetivos explicitos desta v1 (backlog em aberto, ver learnings.md
do projeto):
- IAM/auth: ja resolvido na borda HTTP (X-API-Key por endpoint,
  DA-18/DA-23) - nao duplicado aqui.
- PII/DLP de verdade: um scanner de dados sensiveis no CONTEUDO do
  prompt. sanitize_untrusted_input (app/agent/nodes.py) protege contra
  prompt injection, nao e um scanner de PII - permanece pendente.
- Tenant isolation: o projeto ainda e single-tenant.
- Circuit breaker compartilhado entre replicas: e in-memory, por
  processo - nao compartilhado entre os 2+ pods do deploy Kyma
  (DA-24). Precisaria de um backend compartilhado (Redis, por exemplo)
  para isso em producao multi-instancia.
"""

import logging
import time
from typing import Literal

from app.circuit_breaker import CircuitBreaker
from app.config import Settings, settings
from app.exceptions import ConfigurationError
from app.llm.factory import TRANSPORT_FAILURE_EXCEPTIONS, get_chat_model

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
circuit_breaker = CircuitBreaker()


def classify_sensitivity(state: dict) -> Sensitivity:
    """DA-26: classifica o incidente como 'confidential' ou 'public'
    para decidir se pode ser roteado a um provider cloud.

    Mesmo sinal ja usado em evidence_strength (DA-15) e no Evidence/
    Trust Layer (DA-25): dado real de conector (nao mock, nao
    fallback) e informacao de sistema de producao (status, codigo de
    erro, mensagem reais) - tratado como confidential por padrao.
    Sem dado real de conector (so a descricao textual que o usuario ja
    digitou pra pedir ajuda), classificado como public.
    """
    data = state.get("connector_data")
    if data is not None and not data.is_mock and not data.is_fallback:
        return "confidential"
    return "public"


def _estimate_tokens(text: str) -> int:
    # Heuristica simples (~4 caracteres por token, ordem de grandeza
    # razoavel pra texto tecnico em portugues/ingles) - nao e um
    # tokenizer real, so o suficiente pra um teto de budget. Ver
    # nao-objetivo no docstring do modulo.
    return max(1, len(text) // 4)


def _estimate_cost_usd(provider: str, text: str) -> float:
    tokens = _estimate_tokens(text)
    price = _PRICE_PER_1K_TOKENS_USD.get(provider, 0.0)
    return (tokens / 1000.0) * price


def _select_allowed_providers(sensitivity: Sensitivity, primary: str, fallback: str) -> list[str]:
    """Aplica a policy de roteamento. Para dado confidencial, um
    provider cloud NUNCA entra na lista - nem como fallback."""
    candidates = [primary] + ([fallback] if fallback else [])
    # remove duplicatas preservando ordem (primario tem prioridade)
    seen: set[str] = set()
    candidates = [p for p in candidates if not (p in seen or seen.add(p))]

    if sensitivity == "public":
        return candidates
    return [p for p in candidates if PROVIDER_LOCALITY.get(p) == "local"]


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
    allowed = _select_allowed_providers(sensitivity, cfg.llm_provider, cfg.llm_fallback_provider)

    if not allowed:
        raise PolicyViolationError(
            f"AI Gateway: incidente classificado como '{sensitivity}' - nenhum "
            f"provider permitido pela policy (primario='{cfg.llm_provider}', "
            f"fallback='{cfg.llm_fallback_provider or '(nenhum)'}''). Dado "
            "confidencial so pode ser roteado para um provider local (ollama)."
        )

    last_error: Exception | None = None
    for provider in allowed:
        if circuit_breaker.is_open(provider, cfg.llm_gateway_circuit_cooldown_seconds):
            logger.warning(
                "AI Gateway audit: provider=%s sensitivity=%s status=circuit_open - pulando sem tentar",
                provider,
                sensitivity,
            )
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

        started_at = time.monotonic()
        try:
            result = build_and_invoke(llm)
        except TRANSPORT_FAILURE_EXCEPTIONS as exc:
            latency = time.monotonic() - started_at
            circuit_breaker.record_failure(provider, cfg.llm_gateway_circuit_failure_threshold)
            logger.warning(
                "AI Gateway audit: provider=%s sensitivity=%s status=failure "
                "latency_s=%.2f error=%s",
                provider,
                sensitivity,
                latency,
                exc,
            )
            last_error = exc
            continue
        else:
            latency = time.monotonic() - started_at
            circuit_breaker.record_success(provider)
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
