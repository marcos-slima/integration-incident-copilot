"""DA-48 — captura de tokens REAIS nas respostas LLM (metering).

Motivacao: ate aqui o teto de custo do AI Gateway (DA-26) usava apenas
uma ESTIMATIVA por heuristica de caracteres (gateway._estimate_cost_usd)
— nenhum numero real de tokens era observado nem persistido. Este modulo
preenche a lacuna: um callback LangChain (BaseCallbackHandler) anexado
ao chat model captura o `usage` que o provider devolveu de verdade e o
gateway persiste em `llm_usage` (best-effort, via
app/admin/repository.py::record_usage).

Fontes de usage suportadas (on_llm_end / LLMResult.generation_info):
  - OpenAI-compatible (langchain-openai): generation_info["token_usage"]
    = {prompt_tokens, completion_tokens, ...}
  - Ollama (langchain-ollama): generation_info["prompt_eval_count"] /
    generation_info["eval_count"] (nomes do proprio runtime Ollama).
  - Fallback generico: LLMResult.llm_output["token_usage"].
Sem `usage` na resposta (provider que nao reporta) → nada e gravado
(sem heuristica de estimativa aqui; o teto de budget continua o do
gateway).

Precos usados na captura espelham a tabela interna do gateway
($10/1M in&out para openai/azure_openai, 0 para ollama). O preco do
registro (llm_models.price_*) tera prioridade numa proxima fase — a
parcela administrativa do custo ja fica no resumo via percentual.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)

# USD por 1 milhao de tokens (mesma base da estimativa do gateway:
# $0.01/1k → $10/1M).
_PRICE_IN_PER_1M: dict[str, float] = {"openai": 10.0, "azure_openai": 10.0, "ollama": 0.0}
_PRICE_OUT_PER_1M: dict[str, float] = {"openai": 10.0, "azure_openai": 10.0, "ollama": 0.0}


def _extract_tokens(response: LLMResult) -> tuple[int, int] | None:
    """Extrai (tokens_in, tokens_out) reais de um LLMResult, ou None.

    Ordem de tentativa:
      1. generation_info.token_usage (OpenAI-compatible)
      2. generation_info.prompt_eval_count / eval_count (Ollama)
      3. llm_output.token_usage (fallback generico OpenAI)
    """
    generation: Any = None
    try:
        generation = response.generations[0][0]
    except (IndexError, AttributeError):
        pass
    info = getattr(generation, "generation_info", None) or {}
    if isinstance(info, dict):
        token_usage = info.get("token_usage")
        if isinstance(token_usage, dict) and (
            "prompt_tokens" in token_usage or "completion_tokens" in token_usage
        ):
            return int(token_usage.get("prompt_tokens") or 0), int(
                token_usage.get("completion_tokens") or 0
            )
        prompt_eval = info.get("prompt_eval_count")
        eval_count = info.get("eval_count")
        if prompt_eval is not None or eval_count is not None:
            return int(prompt_eval or 0), int(eval_count or 0)
    llm_output = getattr(response, "llm_output", None) or {}
    if isinstance(llm_output, dict):
        token_usage = llm_output.get("token_usage")
        if isinstance(token_usage, dict) and (
            "prompt_tokens" in token_usage or "completion_tokens" in token_usage
        ):
            return int(token_usage.get("prompt_tokens") or 0), int(
                token_usage.get("completion_tokens") or 0
            )
    return None


class UsageCaptureCallback(BaseCallbackHandler):
    """Acumula tokens reais das respostas de um chat model.

    Anexado ao model via `llm.callbacks = [cb]` em attach_metering().
    Nao toca o fluxo: apenas le LLMResult no on_llm_end e soma."""

    def __init__(self, provider: str, model_name: str) -> None:
        self.provider = provider
        self.model_name = model_name
        self.tokens_in = 0
        self.tokens_out = 0
        self.requests = 0
        self.has_usage = False

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        tokens = _extract_tokens(response)
        if tokens is None:
            return
        tokens_in, tokens_out = tokens
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.requests += 1
        self.has_usage = True


def attach_metering(
    llm: Any, provider: str, model_name: str, enabled: bool
) -> UsageCaptureCallback | None:
    """Anexa o callback de captura a um chat model LangChain.

    Retorna None quando disabled (nao instrumenta nada) ou quando o
    model nao expoe `callbacks`. `enabled` deve levar em conta o
    DATABASE_URL: sem banco nao ha onde persistir."""
    if not enabled:
        return None
    if not hasattr(llm, "callbacks"):
        return None
    cb = UsageCaptureCallback(provider=provider, model_name=model_name)
    try:
        llm.callbacks = [cb]
    except Exception:  # noqa: BLE001 - pragma: no cover; contratos de model mudam entre versoes
        logger.debug("metering(DA-48): nao foi possivel anexar callback a %s", type(llm).__name__)
        return None
    return cb


def _cost_usd(provider: str, tokens_in: int, tokens_out: int) -> float:
    pin = _PRICE_IN_PER_1M.get(provider, 0.0)
    pout = _PRICE_OUT_PER_1M.get(provider, 0.0)
    return (tokens_in * pin + tokens_out * pout) / 1_000_000.0


def record_usage_observed(cb: UsageCaptureCallback | None, *, successful: bool) -> None:
    """Persiste o uso capturado (best-effort). Chamado pelo gateway."""

    if cb is None:
        return
    from app.admin.repository import record_usage

    if not successful and not cb.has_usage:
        # falha de transporte sem nenhum usage reportado: conta so a falha
        record_usage(
            cb.provider,
            cb.model_name,
            tokens_in=0,
            tokens_out=0,
            requests=0,
            failures=1,
            cost_usd=0.0,
        )
        return
    if not cb.has_usage:
        return
    cost = _cost_usd(cb.provider, cb.tokens_in, cb.tokens_out)
    record_usage(
        cb.provider,
        cb.model_name,
        tokens_in=cb.tokens_in,
        tokens_out=cb.tokens_out,
        requests=cb.requests,
        failures=0,
        cost_usd=cost,
    )
