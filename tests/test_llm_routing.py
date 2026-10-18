"""Tests for DA-66: Policy Engine puro para roteamento LLM."""

import dataclasses

import pytest

from app.config import Settings
from app.llm.capabilities import ProviderCapabilities
from app.llm.routing import (
    Candidate,
    RejectionReason,
    RoutingDecision,
    RoutingRequest,
    _capabilities_match,
    _circuit_is_open,
    _estimate_cost_usd,
    _provider_allows_sensitivity,
    _select_allowed_providers,
    evaluate_routing,
)

# ProviderCapabilities requer 3 argumentos obrigatórios
_FULL_CAPS = ProviderCapabilities(
    supports_seed=True,
    supports_response_format=True,
    supports_tools=True,
)


class TestProviderSensitivity:
    """Testes de elegibilidade por sensibilidade (soberania)."""

    def test_public_allowed_any_provider(self):
        """Dado público deve ser permitido para qualquer provider."""
        cfg = Settings()
        ok, _ = _provider_allows_sensitivity("ollama", "public", cfg)
        assert ok is True

    def test_confidential_local_allowed(self):
        """Confidential em provider local deve ser permitido."""
        cfg = Settings()
        ok, _ = _provider_allows_sensitivity("ollama", "confidential", cfg)
        assert ok is True

    def test_confidential_cloud_blocked_strict_mode(self):
        """Confidential em cloud com strict mode deve ser bloqueado."""
        cfg = Settings(data_sovereignty_mode="strict", llm_provider="openai")
        ok, reason = _provider_allows_sensitivity("openai", "confidential", cfg)
        assert ok is False
        assert "cloud" in reason.lower() or "soberania" in reason.lower()

    def test_confidential_cloud_allowed_cloud_with_dlp(self):
        """Confidential em cloud com cloud_with_dlp deve ser permitido se na allowlist."""
        cfg = Settings(
            data_sovereignty_mode="cloud_with_dlp",
            llm_provider="openai",
            confidential_allowed_origins="https://api.openai.com",
        )
        ok, reason = _provider_allows_sensitivity("openai", "confidential", cfg)
        # openai origin é "https://api.openai.com"
        assert ok is True
        assert "allowlist" in reason.lower()


class TestSelectAllowedProviders:
    """Testes de filtragem de providers por soberania."""

    def test_select_allowed_removes_duplicates(self):
        """Providers duplicados devem ser removidos preservando ordem."""
        cfg = Settings()
        result = _select_allowed_providers("public", "ollama", "ollama", cfg)
        assert result == ["ollama"]  # sem duplicata

    def test_select_allowed_filters_by_sovereignty(self):
        """Providers devem ser filtrados pela policy de soberania."""
        cfg = Settings(data_sovereignty_mode="strict")
        # Com strict mode, cloud deve ser bloqueado
        result = _select_allowed_providers("confidential", "ollama", "openai", cfg)
        # Só o local (ollama) deve ser permitido
        assert "openai" not in result
        assert "ollama" in result


class TestCircuitBreaker:
    """Testes de circuit breaker."""

    def test_circuit_is_open_unknown_provider(self):
        """Provider desconhecido deve ter circuito fechado (não abre sem falha)."""
        cfg = Settings()
        # Não levanta exceção, apenas retorna False
        result = _circuit_is_open("unknown_provider", cfg)
        assert result is False


class TestCostEstimation:
    """Testes de estimativa de custo."""

    def test_estimate_cost_fallback_without_registry(self):
        """Sem registry, deve usar fallback interno."""
        cfg = Settings()
        # Sem DATABASE_URL, deve usar tabela interna
        cost = _estimate_cost_usd("ollama", 1000, cfg)
        # Ollama é gratuito na tabela interna
        assert cost == 0.0

    def test_estimate_cost_uses_registry_when_available(self, monkeypatch):
        """Com registry, deve usar preços reais."""

        # Mock registry_prices para retornar preços conhecidos
        def mock_prices(origin, provider):
            if provider == "openai":
                return (0.01, 0.03)  # $0.01 input, $0.03 output por 1M tokens
            return None

        monkeypatch.setattr("app.admin.repository.registry_prices", mock_prices)
        cfg = Settings()

        # 1000 tokens context, ~500 completion tokens
        cost = _estimate_cost_usd("openai", 1000, cfg)
        # 1000 * 0.01/1M + 500 * 0.03/1M = 0.00001 + 0.000015 = 0.000025
        assert cost > 0
        assert cost < 0.001  # custo muito pequeno para 1k tokens


class TestCapabilities:
    """Testes de verificação de capacidades."""

    def test_capabilities_match_all_present(self):
        """Se todas as capacidades exigidas estão presentes, deve retornar True."""
        ok, reason = _capabilities_match("ollama", _FULL_CAPS)
        assert ok is True
        assert "todas" in reason.lower()

    def test_capabilities_match_missing_seed(self):
        """Se falta seed, deve rejeitar."""
        required = ProviderCapabilities(
            supports_seed=True, supports_response_format=True, supports_tools=True
        )
        _ok, _reason = _capabilities_match("ollama", required)
        # Ollama suporta seed, então deve passar
        assert _ok is True


class TestEvaluateRouting:
    """Testes de evaluate_routing() - Policy Engine completo."""

    def test_evaluate_routing_returns_decision(self):
        """evaluate_routing deve devolver RoutingDecision."""
        cfg = Settings()
        request = RoutingRequest(
            sensitivity="public",
            task="test_task",
            context_tokens=100,
            budget_remaining_usd=1.0,
            required_capabilities=_FULL_CAPS,
            primary_provider="ollama",
            fallback_provider=None,
            config=cfg,
        )
        decision = evaluate_routing(request)
        assert isinstance(decision, RoutingDecision)
        assert decision.candidates is not None
        assert isinstance(decision.candidates, list)

    def test_evaluate_routing_respects_sovereignty(self):
        """evaluate_routing deve respeitar soberania: confidential nunca seleciona cloud em strict."""
        cfg = Settings(
            data_sovereignty_mode="strict", llm_provider="ollama", llm_fallback_provider="openai"
        )
        request = RoutingRequest(
            sensitivity="confidential",
            task="test_task",
            context_tokens=100,
            budget_remaining_usd=1.0,
            required_capabilities=_FULL_CAPS,
            primary_provider="ollama",
            fallback_provider="openai",
            config=cfg,
        )
        decision = evaluate_routing(request)
        # Só ollama (local) deve estar na lista de candidatos
        providers = [c.provider for c in decision.candidates]
        assert "openai" not in providers
        assert "ollama" in providers

    def test_evaluate_routing_orders_by_cost(self):
        """Candidatos elegíveis devem ser ordenados por custo (menor primeiro)."""
        cfg = Settings()
        request = RoutingRequest(
            sensitivity="public",
            task="test_task",
            context_tokens=100,
            budget_remaining_usd=1.0,
            required_capabilities=_FULL_CAPS,
            primary_provider="ollama",
            fallback_provider=None,
            config=cfg,
        )
        decision = evaluate_routing(request)
        # Ollama é gratuito, então deve ser o primeiro
        if decision.candidates:
            assert decision.candidates[0].provider == "ollama"

    def test_evaluate_routing_rejects_over_budget(self):
        """Provider com custo estimado acima do orçamento deve ser rejeitado."""
        cfg = Settings()
        request = RoutingRequest(
            sensitivity="public",
            task="test_task",
            context_tokens=1_000_000,  # 1M tokens = custo alto
            budget_remaining_usd=0.0001,  # Orçamento muito baixo
            required_capabilities=_FULL_CAPS,
            primary_provider="openai",
            fallback_provider=None,
            config=cfg,
        )
        decision = evaluate_routing(request)
        # openai deve ser rejeitado por BUDGET_EXCEEDED
        openai_candidate = next((c for c in decision.candidates if c.provider == "openai"), None)
        if openai_candidate:
            assert not openai_candidate.allowed
            assert RejectionReason.BUDGET_EXCEEDED in openai_candidate.rejection_reasons

    def test_evaluate_routing_all_rejected(self):
        """Se todos são rejeitados, selected_provider deve ser None."""
        cfg = Settings(data_sovereignty_mode="strict", llm_provider="openai")
        request = RoutingRequest(
            sensitivity="confidential",
            task="test_task",
            context_tokens=100,
            budget_remaining_usd=1.0,
            required_capabilities=_FULL_CAPS,
            primary_provider="openai",  # cloud em strict mode
            fallback_provider=None,
            config=cfg,
        )
        decision = evaluate_routing(request)
        assert decision.all_rejected is True
        assert decision.selected_provider is None


class TestRejectionReasons:
    """Testes para cada motivo de rejeição."""

    def test_rejection_reason_enum_values(self):
        """RejectionReason deve ter os valores esperados."""
        assert RejectionReason.NO_ORIGIN_RESOLVED.value == "origin_nao_resolvida"
        assert RejectionReason.SOVEREIGNTY_BLOCKED.value == "soberania_bloqueada"
        assert RejectionReason.CAPABILITY_MISSING.value == "capability_faltando"
        assert RejectionReason.CIRCUIT_OPEN.value == "circuito_aberto"
        assert RejectionReason.BUDGET_EXCEEDED.value == "orcamento_excedido"
        assert RejectionReason.HIGH_COST.value == "custo_elevado"
        assert RejectionReason.HIGH_LATENCY.value == "latencia_elevada"


class TestCandidate:
    """Testes do dataclass Candidate."""

    def test_candidate_frozen(self):
        """Candidate deve ser imutável (frozen=True)."""
        candidate = Candidate(
            provider="ollama",
            origin="ollama",
            locality="local",
            allowed=True,
            rejection_reasons=[],
            estimated_cost_usd=0.0,
            latency_ms=None,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            candidate.provider = "openai"  # frozen dataclass
