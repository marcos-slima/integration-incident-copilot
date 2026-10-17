"""DA-68: Matriz de falhas e failover tipado.

Taxonomia fechada de falhas que o AI Gateway pode encontrar ao chamar
providers LLM, com política de resposta por classe.

Classes de falha:
- transient: retry com backoff (ex: falha de conexão transitória)
- rate_limited/quota: retry com backoff + redução de carga (ex: 429 Too Many Requests)
- unavailable: retry com backoff (ex: 503 Service Unavailable)
- timeout: retry com backoff (ex: timeout de rede)
- context_limit: não retry (reduzir contexto) (ex: 400 context length exceeded)
- output_limit: não retry (reduzir output) (ex: 400 output length exceeded)
- budget_exhausted: não retry (parar) (ex: orçamento estourado)
- validation_error: não retry (corrigir input) (ex: 400 invalid request)
- quality_failure: não retry (revisar prompt) (ex: 400 prompt contains prohibited content)

Cada classe define:
- retry? (bool)
- backoff? (bool)
- abre circuito? (bool)
- failover? (bool)
- escala? (bool)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class FailureClass(Enum):
    """Classes de falha fechadas para DA-68."""

    TRANSIENT = auto()  # retry com backoff
    RATE_LIMITED = auto()  # retry com backoff + redução de carga
    UNAVAILABLE = auto()  # retry com backoff
    TIMEOUT = auto()  # retry com backoff
    CONTEXT_LIMIT = auto()  # não retry (reduzir contexto)
    OUTPUT_LIMIT = auto()  # não retry (reduzir output)
    BUDGET_EXHAUSTED = auto()  # não retry (parar)
    VALIDATION_ERROR = auto()  # não retry (corrigir input)
    QUALITY_FAILURE = auto()  # não retry (revisar prompt)


@dataclass(frozen=True)
class FailurePolicy:
    """Política de resposta a uma classe de falha."""

    retry: bool
    backoff: bool
    open_circuit: bool
    failover: bool
    scale_down: bool


# Mapeamento de classes para políticas
FAILURE_POLICIES: dict[FailureClass, FailurePolicy] = {
    FailureClass.TRANSIENT: FailurePolicy(
        retry=True,
        backoff=True,
        open_circuit=True,
        failover=True,
        scale_down=False,
    ),
    FailureClass.RATE_LIMITED: FailurePolicy(
        retry=True,
        backoff=True,
        open_circuit=True,
        failover=True,
        scale_down=True,
    ),
    FailureClass.UNAVAILABLE: FailurePolicy(
        retry=True,
        backoff=True,
        open_circuit=True,
        failover=True,
        scale_down=False,
    ),
    FailureClass.TIMEOUT: FailurePolicy(
        retry=True,
        backoff=True,
        open_circuit=True,
        failover=True,
        scale_down=False,
    ),
    FailureClass.CONTEXT_LIMIT: FailurePolicy(
        retry=False,
        backoff=False,
        open_circuit=False,
        failover=False,
        scale_down=False,
    ),
    FailureClass.OUTPUT_LIMIT: FailurePolicy(
        retry=False,
        backoff=False,
        open_circuit=False,
        failover=False,
        scale_down=False,
    ),
    FailureClass.BUDGET_EXHAUSTED: FailurePolicy(
        retry=False,
        backoff=False,
        open_circuit=False,
        failover=False,
        scale_down=False,
    ),
    FailureClass.VALIDATION_ERROR: FailurePolicy(
        retry=False,
        backoff=False,
        open_circuit=False,
        failover=False,
        scale_down=False,
    ),
    FailureClass.QUALITY_FAILURE: FailurePolicy(
        retry=False,
        backoff=False,
        open_circuit=False,
        failover=False,
        scale_down=False,
    ),
}


def classify_failure(exc: BaseException) -> FailureClass:
    """Classifica uma exceção em uma das classes de falha.

    Args:
        exc: A exceção a ser classificada.

    Returns:
        A classe de falha correspondente.

    Raises:
        ValueError: Se a exceção não puder ser classificada.
    """
    # Erros de transporte (conexão, timeout, etc.)
    # NOTA: TimeoutError deve ser verificado ANTES de ConnectionError
    # porque TimeoutError é uma subclasse de ConnectionError em Python 3.11+
    if isinstance(exc, TimeoutError):
        return FailureClass.TIMEOUT

    if isinstance(exc, ConnectionError):
        return FailureClass.TRANSIENT

    # Erros de quota/rate limit (429)
    if hasattr(exc, "status_code") and exc.status_code == 429:
        # Verifica se é orçamento primeiro
        if "budget" in str(exc).lower() or "quota" in str(exc).lower():
            return FailureClass.BUDGET_EXHAUSTED
        return FailureClass.RATE_LIMITED

    # Erros de serviço indisponível (503)
    if hasattr(exc, "status_code") and exc.status_code == 503:
        return FailureClass.UNAVAILABLE

    # Erros de cliente (400)
    if hasattr(exc, "status_code") and exc.status_code == 400:
        exc_str = str(exc).lower()
        if "output" in exc_str:
            return FailureClass.OUTPUT_LIMIT
        if "context" in exc_str or "length" in exc_str:
            return FailureClass.CONTEXT_LIMIT
        if "prompt" in exc_str or "content" in exc_str:
            return FailureClass.QUALITY_FAILURE
        return FailureClass.VALIDATION_ERROR

    # Fallback para erros de transporte genéricos
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return FailureClass.TRANSIENT

    raise ValueError(f"Não foi possível classificar a exceção: {exc}")


def get_failure_policy(exc: BaseException) -> FailurePolicy:
    """Obtém a política de resposta para uma exceção.

    Args:
        exc: A exceção a ser classificada.

    Returns:
        A política de resposta correspondente.
    """
    failure_class = classify_failure(exc)
    return FAILURE_POLICIES[failure_class]
