"""DA-48 — testes da captura de tokens reais (metering).

Cobre a extracao de usage de LLMResult nos formatos OpenAI-compatible
(token_usage) e Ollama (prompt_eval_count/eval_count), a acumulacao no
callback, o anexo/desanexo no model, o custo estimado por provider e o
no-op best-effort sem DATABASE_URL.
"""

from __future__ import annotations

from langchain_core.outputs import Generation, LLMResult

from app.admin.metering import (
    UsageCaptureCallback,
    _cost_usd,
    _extract_tokens,
    attach_metering,
    record_usage_observed,
)
from app.config import settings


class StubLLM:
    def __init__(self):
        self.callbacks = None
        self.model_name = "stub-model"


def _llm_result_openai(prompt: int, completion: int) -> LLMResult:
    return LLMResult(
        generations=[
            [
                Generation(
                    text="ok",
                    generation_info={
                        "token_usage": {
                            "prompt_tokens": prompt,
                            "completion_tokens": completion,
                            "total_tokens": prompt + completion,
                        }
                    },
                )
            ]
        ],
        llm_output=None,
    )


def _llm_result_ollama(prompt: int, completion: int) -> LLMResult:
    return LLMResult(
        generations=[
            [
                Generation(
                    text="ok",
                    generation_info={
                        "prompt_eval_count": prompt,
                        "eval_count": completion,
                        "load_duration": 0,
                    },
                )
            ]
        ],
        llm_output=None,
    )


def _llm_result_sem_usage() -> LLMResult:
    return LLMResult(
        generations=[[Generation(text="sem usage", generation_info=None)]], llm_output=None
    )


def test_extract_openai_token_usage():
    assert _extract_tokens(_llm_result_openai(100, 30)) == (100, 30)


def test_extract_ollama_eval_counts():
    assert _extract_tokens(_llm_result_ollama(80, 20)) == (80, 20)


def test_extract_sem_usage_retorna_none():
    assert _extract_tokens(_llm_result_sem_usage()) is None


def test_callback_acumula_entre_chamadas():
    cb = UsageCaptureCallback(provider="openai", model_name="m")
    cb.on_llm_end(_llm_result_openai(100, 30))
    cb.on_llm_end(_llm_result_openai(50, 10))
    assert (cb.tokens_in, cb.tokens_out, cb.requests) == (150, 40, 2)
    assert cb.has_usage is True


def test_callback_ignora_resposta_sem_usage():
    cb = UsageCaptureCallback(provider="ollama", model_name="m")
    cb.on_llm_end(_llm_result_sem_usage())
    assert cb.has_usage is False
    assert cb.requests == 0


def test_attach_desabilitado_retorna_none():
    assert attach_metering(StubLLM(), "ollama", "m", enabled=False) is None


def test_attach_anexa_callback():
    llm = StubLLM()
    cb = attach_metering(llm, "ollama", "m", enabled=True)
    assert cb is not None
    assert llm.callbacks == [cb]
    assert llm.callbacks[0].provider == "ollama"


def test_attach_sem_callbacks_retorna_none():
    class SemCallbacks:
        pass

    assert attach_metering(SemCallbacks(), "ollama", "m", enabled=True) is None


def test_custo_por_provider():
    # $10/1M in e out p/ openai: 1M in + 0.5M out = $10 + $5 = $15
    assert _cost_usd("openai", 1_000_000, 500_000) == 15.0
    assert _cost_usd("ollama", 1_000_000, 1_000_000) == 0.0


def test_record_usage_observed_noop_sem_banco(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "")
    cb = UsageCaptureCallback(provider="openai", model_name="m")
    cb.on_llm_end(_llm_result_openai(100, 30))
    assert cb.has_usage is True
    record_usage_observed(cb, successful=True)  # sem banco: no-op sem excecao


def test_record_usage_observed_com_cb_none():
    record_usage_observed(None, successful=True)  # nada a registrar
