"""DA-68: testes da taxonomia de falhas e failover tipado."""

import pytest

from app.llm.failures import (
    FAILURE_POLICIES,
    FailureClass,
    classify_failure,
    get_failure_policy,
)


class MockException:
    """Mock de exceção com status_code."""

    def __init__(self, status_code: int, message: str = ""):
        self.status_code = status_code
        self.message = message

    def __str__(self) -> str:
        return self.message or f"HTTP {self.status_code}"


class TestFailureClass:
    """Testes da enumeração FailureClass."""

    def test_failure_class_has_all_expected_values(self):
        """Verifica que todas as classes esperadas estão presentes."""
        expected = {
            "TRANSIENT",
            "RATE_LIMITED",
            "UNAVAILABLE",
            "TIMEOUT",
            "CONTEXT_LIMIT",
            "OUTPUT_LIMIT",
            "BUDGET_EXHAUSTED",
            "VALIDATION_ERROR",
            "QUALITY_FAILURE",
        }
        actual = {e.name for e in FailureClass}
        assert expected == actual


class TestFailurePolicy:
    """Testes da política de falha."""

    def test_transient_policy(self):
        """transient: retry com backoff, abre circuito, failover."""
        policy = FAILURE_POLICIES[FailureClass.TRANSIENT]
        assert policy.retry is True
        assert policy.backoff is True
        assert policy.open_circuit is True
        assert policy.failover is True
        assert policy.scale_down is False

    def test_rate_limited_policy(self):
        """rate_limited: retry com backoff, reduz carga."""
        policy = FAILURE_POLICIES[FailureClass.RATE_LIMITED]
        assert policy.retry is True
        assert policy.backoff is True
        assert policy.open_circuit is True
        assert policy.failover is True
        assert policy.scale_down is True

    def test_unavailable_policy(self):
        """unavailable: retry com backoff."""
        policy = FAILURE_POLICIES[FailureClass.UNAVAILABLE]
        assert policy.retry is True
        assert policy.backoff is True
        assert policy.open_circuit is True
        assert policy.failover is True
        assert policy.scale_down is False

    def test_timeout_policy(self):
        """timeout: retry com backoff."""
        policy = FAILURE_POLICIES[FailureClass.TIMEOUT]
        assert policy.retry is True
        assert policy.backoff is True
        assert policy.open_circuit is True
        assert policy.failover is True
        assert policy.scale_down is False

    def test_context_limit_policy(self):
        """context_limit: não retry (reduzir contexto)."""
        policy = FAILURE_POLICIES[FailureClass.CONTEXT_LIMIT]
        assert policy.retry is False
        assert policy.backoff is False
        assert policy.open_circuit is False
        assert policy.failover is False
        assert policy.scale_down is False

    def test_output_limit_policy(self):
        """output_limit: não retry (reduzir output)."""
        policy = FAILURE_POLICIES[FailureClass.OUTPUT_LIMIT]
        assert policy.retry is False
        assert policy.backoff is False
        assert policy.open_circuit is False
        assert policy.failover is False
        assert policy.scale_down is False

    def test_budget_exhausted_policy(self):
        """budget_exhausted: não retry (parar)."""
        policy = FAILURE_POLICIES[FailureClass.BUDGET_EXHAUSTED]
        assert policy.retry is False
        assert policy.backoff is False
        assert policy.open_circuit is False
        assert policy.failover is False
        assert policy.scale_down is False

    def test_validation_error_policy(self):
        """validation_error: não retry (corrigir input)."""
        policy = FAILURE_POLICIES[FailureClass.VALIDATION_ERROR]
        assert policy.retry is False
        assert policy.backoff is False
        assert policy.open_circuit is False
        assert policy.failover is False
        assert policy.scale_down is False

    def test_quality_failure_policy(self):
        """quality_failure: não retry (revisar prompt)."""
        policy = FAILURE_POLICIES[FailureClass.QUALITY_FAILURE]
        assert policy.retry is False
        assert policy.backoff is False
        assert policy.open_circuit is False
        assert policy.failover is False
        assert policy.scale_down is False


class TestClassifyFailure:
    """Testes da função classify_failure."""

    def test_connection_error_is_transient(self):
        """ConnectionError -> TRANSIENT."""
        exc = ConnectionError("connection refused")
        assert classify_failure(exc) == FailureClass.TRANSIENT

    def test_timeout_error_is_timeout(self):
        """TimeoutError -> TIMEOUT."""
        exc = TimeoutError("timed out")
        assert classify_failure(exc) == FailureClass.TIMEOUT

    def test_http_429_is_rate_limited(self):
        """HTTP 429 -> RATE_LIMITED."""
        exc = MockException(429, "rate limit exceeded")
        assert classify_failure(exc) == FailureClass.RATE_LIMITED

    def test_http_503_is_unavailable(self):
        """HTTP 503 -> UNAVAILABLE."""
        exc = MockException(503, "service unavailable")
        assert classify_failure(exc) == FailureClass.UNAVAILABLE

    def test_http_400_context_length_is_context_limit(self):
        """HTTP 400 com context -> CONTEXT_LIMIT."""
        exc = MockException(400, "context length exceeded")
        assert classify_failure(exc) == FailureClass.CONTEXT_LIMIT

    def test_http_400_output_length_is_output_limit(self):
        """HTTP 400 com output -> OUTPUT_LIMIT."""
        exc = MockException(400, "output length exceeded")
        assert classify_failure(exc) == FailureClass.OUTPUT_LIMIT

    def test_http_400_validation_error(self):
        """HTTP 400 genérico -> VALIDATION_ERROR."""
        exc = MockException(400, "invalid request")
        assert classify_failure(exc) == FailureClass.VALIDATION_ERROR

    def test_http_400_quality_failure(self):
        """HTTP 400 com prompt/content -> QUALITY_FAILURE."""
        exc = MockException(400, "prompt contains prohibited content")
        assert classify_failure(exc) == FailureClass.QUALITY_FAILURE

    def test_unclassifiable_raises_value_error(self):
        """Exceção não classificável -> ValueError."""
        exc = ValueError("unknown error")
        with pytest.raises(ValueError, match="Não foi possível classificar"):
            classify_failure(exc)


class TestGetFailurePolicy:
    """Testes da função get_failure_policy."""

    def test_get_policy_for_transient(self):
        """get_failure_policy(ConnectionError) -> TRANSIENT policy."""
        exc = ConnectionError("connection refused")
        policy = get_failure_policy(exc)
        assert policy == FAILURE_POLICIES[FailureClass.TRANSIENT]

    def test_get_policy_for_rate_limited(self):
        """get_failure_policy(HTTP 429) -> RATE_LIMITED policy."""
        exc = MockException(429, "rate limit exceeded")
        policy = get_failure_policy(exc)
        assert policy == FAILURE_POLICIES[FailureClass.RATE_LIMITED]
