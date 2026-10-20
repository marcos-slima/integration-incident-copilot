"""Testes para métricas FinOps (DA-70).

Testam as funções de observação e gravação de métricas:
- observe_llm_request, observe_llm_tokens, observe_llm_cost
- record_llm_request, record_llm_tokens, record_llm_cost
"""

from unittest.mock import MagicMock, patch

from app.metrics import (
    observe_llm_cost,
    observe_llm_request,
    observe_llm_tokens,
)


class TestObserveMetrics:
    """Testa funções de observação de métricas Prometheus."""

    @patch("app.metrics._PROMETHEUS_AVAILABLE", True)
    @patch("app.metrics.LLM_REQUESTS_TOTAL")
    def test_observe_llm_request(self, mock_metric: MagicMock) -> None:
        """DA-70: observe_llm_request incrementa a métrica Prometheus."""
        observe_llm_request(provider="ollama", model="qwen3.6:35b-a3b")

        mock_metric.labels.assert_called_once_with(provider="ollama", model="qwen3.6:35b-a3b")
        mock_metric.labels().inc.assert_called_once()

    @patch("app.metrics._PROMETHEUS_AVAILABLE", True)
    @patch("app.metrics.LLM_TOKENS_TOTAL")
    def test_observe_llm_tokens(self, mock_metric: MagicMock) -> None:
        """DA-70: observe_llm_tokens incrementa métricas input/output."""
        observe_llm_tokens(
            provider="openai",
            model="gpt-4o-mini",
            input_tokens=100,
            output_tokens=50,
        )

        assert mock_metric.labels.call_count == 2
        mock_metric.labels.assert_any_call(
            provider="openai", model="gpt-4o-mini", token_type="input"
        )
        mock_metric.labels.assert_any_call(
            provider="openai", model="gpt-4o-mini", token_type="output"
        )

    @patch("app.metrics._PROMETHEUS_AVAILABLE", True)
    @patch("app.metrics.LLM_COST_USD_TOTAL")
    def test_observe_llm_cost(self, mock_metric: MagicMock) -> None:
        """DA-70: observe_llm_cost incrementa a métrica de custo."""
        observe_llm_cost(
            provider="azure_openai",
            model="gpt-4",
            cost_usd=0.0025,
        )

        mock_metric.labels.assert_called_once_with(provider="azure_openai", model="gpt-4")
        mock_metric.labels().inc.assert_called_once_with(0.0025)

    @patch("app.metrics._PROMETHEUS_AVAILABLE", False)
    def test_observe_metrics_stubs(self) -> None:
        """DA-70: funções não levantam quando Prometheus não está disponível."""
        # Não deve levantar exceção
        observe_llm_request(provider="ollama", model="qwen3.6:35b-a3b")
        observe_llm_tokens(
            provider="ollama", model="qwen3.6:35b-a3b", input_tokens=10, output_tokens=5
        )
        observe_llm_cost(provider="ollama", model="qwen3.6:35b-a3b", cost_usd=0.001)


class TestRecordMetrics:
    """Testa funções de gravação no banco de dados."""

    def test_record_llm_request_no_exception(self) -> None:
        """DA-70: record_llm_request não levanta exceção (mock)."""
        with (
            patch("app.admin.repository.get_sync_session_factory"),
            patch("app.admin.repository.settings") as mock_settings,
        ):
            mock_settings.database_url = "postgresql://user:pass@localhost/iic"
            from app.metrics import record_llm_request

            # Não deve levantar exceção
            record_llm_request(provider="ollama", model="qwen3.6:35b-a3b")

    def test_record_llm_tokens_no_exception(self) -> None:
        """DA-70: record_llm_tokens não levanta exceção (mock)."""
        with (
            patch("app.admin.repository.get_sync_session_factory"),
            patch("app.admin.repository.settings") as mock_settings,
        ):
            mock_settings.database_url = "postgresql://user:pass@localhost/iic"
            from app.metrics import record_llm_tokens

            # Não deve levantar exceção
            record_llm_tokens(
                provider="openai",
                model="gpt-4o-mini",
                input_tokens=100,
                output_tokens=50,
            )

    def test_record_llm_cost_no_exception(self) -> None:
        """DA-70: record_llm_cost não levanta exceção (mock)."""
        with (
            patch("app.admin.repository.get_sync_session_factory"),
            patch("app.admin.repository.settings") as mock_settings,
        ):
            mock_settings.database_url = "postgresql://user:pass@localhost/iic"
            from app.metrics import record_llm_cost

            # Não deve levantar exceção
            record_llm_cost(provider="ollama", model="qwen3.6:35b-a3b", cost_usd=0.001)

    def test_record_metrics_no_db(self) -> None:
        """DA-70: funções não tentam gravar sem DATABASE_URL."""
        with patch("app.admin.repository.settings") as mock_settings:
            mock_settings.database_url = None
            from app.metrics import (
                record_llm_cost,
                record_llm_request,
                record_llm_tokens,
            )

            # Não deve levantar exceção e não deve chamar o banco
            record_llm_request(provider="ollama", model="qwen3.6:35b-a3b")
            record_llm_tokens(
                provider="ollama", model="qwen3.6:35b-a3b", input_tokens=10, output_tokens=5
            )
            record_llm_cost(provider="ollama", model="qwen3.6:35b-a3b", cost_usd=0.001)

    def test_record_metrics_exception_handling(self) -> None:
        """DA-70: exceções no banco não propagam."""
        with (
            patch("app.admin.repository.get_sync_session_factory") as mock_factory,
            patch("app.admin.repository.settings") as mock_settings,
        ):
            mock_factory.return_value.return_value.__enter__.side_effect = Exception("DB error")
            mock_settings.database_url = "postgresql://user:pass@localhost/iic"
            from app.metrics import (
                record_llm_cost,
                record_llm_request,
                record_llm_tokens,
            )

            # Não deve levantar exceção
            record_llm_request(provider="ollama", model="qwen3.6:35b-a3b")
            record_llm_tokens(
                provider="ollama", model="qwen3.6:35b-a3b", input_tokens=10, output_tokens=5
            )
            record_llm_cost(provider="ollama", model="qwen3.6:35b-a3b", cost_usd=0.001)
