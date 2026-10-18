"""DA-72: roteamento por registro de modelos.

Os criterios de aceite da proposta (docs: DA-72) viram um teste cada:
1. todos os habilitados aparecem, com posicao ou motivo;
2. confidential em strict -> so origem local elegivel;
3. janela de contexto; 4. dominio; 5. limite mensal;
6. failover pela lista ordenada, com o modelo que respondeu registrado;
7. modo .env inalterado (suites test_llm_routing/test_llm_gateway).
Mais: invariante 9 emendada (modelo so restringe capacidade), credencial,
destino fora da origem, nivel premium, score/prioridade/default, preview.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.config import Settings
from app.llm.capabilities import ProviderCapabilities
from app.llm.registry_routing import (
    RegistryModel,
    RegistrySnapshot,
    candidate_settings,
    effective_tier,
    effective_transport,
)
from app.llm.routing import RejectionReason, RoutingRequest, evaluate_routing

LOCAL = "http://127.0.0.1:11434"
OPENAI = "https://api.openai.com"
GROQ = "https://api.groq.com"
_SEED_ONLY = ProviderCapabilities(
    supports_seed=True, supports_response_format=False, supports_tools=False
)


def _cfg(**kw) -> Settings:
    base = {
        "_env_file": None,
        "llm_provider": "ollama",
        "llm_fallback_provider": "",
        "ollama_host": LOCAL,
        "llm_registry_db": True,
        "data_sovereignty_mode": "strict",
    }
    base.update(kw)
    return Settings(**base)


def _m(model_id, origin, **kw) -> RegistryModel:
    return RegistryModel(id=kw.pop("id", model_id), provider_origin=origin, model_id=model_id, **kw)


def _snap(*models, creds=(OPENAI, GROQ), usage=None, latency=None) -> RegistrySnapshot:
    return RegistrySnapshot(
        models=tuple(models),
        credentialed_origins=frozenset(creds),
        usage_tokens=usage or {},
        latency_ms=latency or {},
    )


def _decide(
    snapshot, *, sensitivity="public", tokens=4000, domain=None, budget=0.5, caps=None, cfg=None
):
    return evaluate_routing(
        RoutingRequest(
            sensitivity=sensitivity,
            task="diagnosis",
            context_tokens=tokens,
            budget_remaining_usd=budget,
            required_capabilities=caps or _SEED_ONLY,
            primary_provider="ollama",
            fallback_provider=None,
            config=cfg or _cfg(),
            domain=domain,
            registry=snapshot,
        )
    )


def _by_model(decision):
    return {c.model_id: c for c in decision.candidates}


@pytest.fixture(autouse=True)
def _closed_circuits(monkeypatch):
    monkeypatch.setattr("app.llm.routing._circuit_is_open", lambda key, cfg: False)


# --- criterio 1 ---------------------------------------------------------


def test_every_enabled_model_is_a_candidate_with_rank_or_reason():
    d = _decide(
        _snap(
            _m("qwen", LOCAL),
            _m("gpt-4o-mini", OPENAI, price_in_per_1m=0.15, price_out_per_1m=0.6),
            _m("big", GROQ, tier="premium"),
        )
    )
    by = _by_model(d)
    assert set(by) == {"qwen", "gpt-4o-mini", "big"}
    assert by["big"].allowed is False
    assert by["big"].rejection_reasons == [RejectionReason.TIER_NOT_AUTHORIZED]
    assert [c.model_id for c in d.candidates if c.allowed] == ["qwen", "gpt-4o-mini"]
    assert d.selected_provider == "ollama"


# --- criterio 2 ---------------------------------------------------------


def test_confidential_in_strict_only_local_models():
    d = _decide(
        _snap(_m("qwen", LOCAL), _m("gpt-4o-mini", OPENAI), _m("oss", GROQ)),
        sensitivity="confidential",
    )
    allowed = [c.model_id for c in d.candidates if c.allowed]
    assert allowed == ["qwen"]
    assert RejectionReason.SOVEREIGNTY_BLOCKED in _by_model(d)["oss"].rejection_reasons


def test_confidential_cloud_allowed_with_dlp_and_allowlist():
    cfg = _cfg(data_sovereignty_mode="cloud_with_dlp", confidential_allowed_origins=GROQ)
    d = _decide(
        _snap(_m("gpt-4o-mini", OPENAI), _m("oss", GROQ)), sensitivity="confidential", cfg=cfg
    )
    assert [c.model_id for c in d.candidates if c.allowed] == ["oss"]


# --- criterios 3, 4, 5 --------------------------------------------------


def test_context_window_rejects_small_model():
    d = _decide(
        _snap(
            _m("small", LOCAL, context_window_tokens=8000),
            _m("large", LOCAL, id="2", context_window_tokens=128_000),
        ),
        tokens=6000,
    )
    by = _by_model(d)
    assert by["small"].rejection_reasons == [RejectionReason.CONTEXT_WINDOW]  # 6000*1.5 > 8000
    assert by["large"].allowed


def test_domain_restriction():
    d = _decide(
        _snap(_m("sap-only", LOCAL, domains=("sap",)), _m("any", LOCAL, id="2")), domain="saas"
    )
    by = _by_model(d)
    assert by["sap-only"].rejection_reasons == [RejectionReason.DOMAIN_MISMATCH]
    assert by["any"].allowed
    assert _by_model(_decide(_snap(_m("sap-only", LOCAL, domains=("sap",))), domain="sap"))[
        "sap-only"
    ].allowed


def test_monthly_limit_blocks_at_100_percent():
    snap = _snap(
        _m("gpt-4o-mini", OPENAI, monthly_limit_tokens=1000),
        _m("oss", GROQ),
        usage={(OPENAI, "gpt-4o-mini"): 1000},
    )
    by = _by_model(_decide(snap))
    assert by["gpt-4o-mini"].rejection_reasons == [RejectionReason.MONTHLY_LIMIT]
    assert by["oss"].allowed


# --- invariante 9 emendada, credencial, destino, premium ---------------


def test_model_can_restrict_but_never_widen_origin_capability(monkeypatch):
    tools = ProviderCapabilities(
        supports_seed=False, supports_response_format=False, supports_tools=True
    )
    d = _decide(
        _snap(_m("no-tools", LOCAL, supports_tools=False), _m("default", LOCAL, id="2")), caps=tools
    )
    by = _by_model(d)
    assert by["no-tools"].rejection_reasons == [RejectionReason.CAPABILITY_MISSING]
    assert by["default"].allowed
    # origem sem seed (tabela DA-45): True no registro nao amplia
    no_seed = ProviderCapabilities(
        supports_seed=False, supports_response_format=True, supports_tools=True
    )
    monkeypatch.setattr("app.llm.routing.capabilities_for_origin", lambda origin: no_seed)
    d2 = _decide(_snap(_m("claims-seed", GROQ, supports_seed=True)))
    assert _by_model(d2)["claims-seed"].rejection_reasons == [RejectionReason.CAPABILITY_MISSING]


def test_cloud_model_without_credential_is_rejected():
    d = _decide(_snap(_m("oss", GROQ), creds=()))
    assert _by_model(d)["oss"].rejection_reasons == [RejectionReason.CREDENTIAL_MISSING]


def test_base_url_outside_origin_is_rejected():
    d = _decide(_snap(_m("m", GROQ, base_url="https://proxy.terceiro.example.com/v1")))
    assert RejectionReason.DESTINATION_MISMATCH in _by_model(d)["m"].rejection_reasons


def test_budget_uses_registered_price():
    snap = _snap(
        _m("pricey", OPENAI, price_in_per_1m=100.0, price_out_per_1m=300.0),
        _m("cheap", GROQ, price_in_per_1m=0.1, price_out_per_1m=0.2),
    )
    by = _by_model(_decide(snap, tokens=10_000, budget=0.5))
    assert by["pricey"].rejection_reasons == [RejectionReason.BUDGET_EXCEEDED]
    assert by["cheap"].estimated_cost_usd == pytest.approx(10_000 * 0.1e-6 + 5_000 * 0.2e-6)


def test_registry_unavailable_means_no_candidate():
    d = _decide(_snap())
    assert d.all_rejected and d.selected_provider is None


# --- fase 2: score, prioridade, default ---------------------------------


def test_score_weighs_cost_and_latency():
    snap = _snap(
        _m("cheap-slow", GROQ, price_in_per_1m=0.1, price_out_per_1m=0.1),
        _m("dear-fast", OPENAI, price_in_per_1m=0.2, price_out_per_1m=0.2),
        latency={(GROQ, "cheap-slow"): 9000.0, (OPENAI, "dear-fast"): 1000.0},
    )
    by_cost = _decide(snap, cfg=_cfg(llm_routing_cost_weight=1.0, llm_routing_latency_weight=0.0))
    by_lat = _decide(snap, cfg=_cfg(llm_routing_cost_weight=0.0, llm_routing_latency_weight=1.0))
    assert by_cost.candidates[0].model_id == "cheap-slow"
    assert by_lat.candidates[0].model_id == "dear-fast"


def test_tie_broken_by_priority_then_default():
    snap = _snap(
        _m("a", LOCAL, priority=50),
        _m("b", LOCAL, id="2", priority=10),
        _m("c", LOCAL, id="3", priority=10, is_default=True),
    )
    assert [c.model_id for c in _decide(snap).candidates] == ["c", "b", "a"]


def test_unknown_latency_is_not_penalized():
    snap = _snap(_m("new", GROQ), _m("old", OPENAI), latency={(OPENAI, "old"): 500.0})
    d = _decide(snap, cfg=_cfg(llm_routing_cost_weight=0.0, llm_routing_latency_weight=1.0))
    assert {c.model_id: c.score for c in d.candidates} == {"new": 0.0, "old": 0.0}


# --- derivacoes ----------------------------------------------------------


def test_derivations():
    cfg = _cfg()
    assert effective_transport(_m("x", LOCAL), cfg) == "ollama"
    assert effective_transport(_m("x", "https://r.openai.azure.com"), cfg) == "azure_openai"
    assert effective_transport(_m("x", GROQ), cfg) == "openai_compatible"
    assert effective_tier(_m("x", LOCAL)) == "local"
    assert effective_tier(_m("x", GROQ)) == "standard"
    label, c = candidate_settings(_m("oss", GROQ), cfg, api_key="k")
    assert label == "openai" and c.openai_base_url == GROQ + "/v1" and c.llm_model == "oss"
    assert c.openai_api_key == "k" and c.llm_fallback_provider is None


# --- criterio 6: failover pela lista, modelo efetivo --------------------


def test_gateway_fails_over_through_registry_list(monkeypatch):
    from app.llm import gateway

    origin_a, origin_b = (
        f"https://a{uuid.uuid4().hex[:6]}.example.com",
        f"https://b{uuid.uuid4().hex[:6]}.example.com",
    )
    snap = _snap(
        _m("first", origin_a, price_in_per_1m=0.1, price_out_per_1m=0.1),
        _m("second", origin_b, price_in_per_1m=0.2, price_out_per_1m=0.2),
        creds=(origin_a, origin_b),
    )
    monkeypatch.setattr(gateway, "load_registry_snapshot", lambda: snap)
    monkeypatch.setattr("app.admin.repository.registry_credential", lambda origin: "k")
    monkeypatch.setattr(gateway, "get_chat_model", lambda **kw: kw["config"])
    calls = []

    def build_and_invoke(cfg):
        calls.append(cfg.llm_model)
        if cfg.llm_model == "first":
            raise ConnectionError("down")
        return {"ok": True}

    cfg = _cfg(
        llm_gateway_backoff_base_seconds=0.0,
        data_sovereignty_mode="cloud_with_dlp",
        confidential_allowed_origins=f"{origin_a},{origin_b}",
    )
    gateway.consume_last_selection()
    result, provider = gateway.invoke_via_gateway(
        build_and_invoke, state={"description": "x"}, prompt_text="x", config=cfg
    )
    assert result == {"ok": True} and provider == "openai"
    assert calls == ["first", "second"]
    selection = gateway.consume_last_selection()
    assert selection.model_id == "second" and selection.origin == origin_b
    assert gateway.consume_last_selection() is None  # consumida


def test_gateway_registry_all_rejected_raises_policy_violation(monkeypatch):
    from app.llm import gateway
    from app.llm.gateway import PolicyViolationError

    monkeypatch.setattr(gateway, "load_registry_snapshot", lambda: None)  # banco fora
    with pytest.raises(PolicyViolationError, match="registro vazio ou indisponivel"):
        gateway.invoke_via_gateway(
            lambda llm: 1, state={"description": "x"}, prompt_text="x", config=_cfg()
        )


def test_env_mode_does_not_read_registry(monkeypatch):
    from app.llm import gateway

    monkeypatch.setattr(
        gateway, "load_registry_snapshot", lambda: pytest.fail("modo .env nao le o registro")
    )
    decision, _ = gateway.preview_routing("public", None, 100, config=_cfg(llm_registry_db=False))
    assert decision.request.registry is None
    assert [c.provider for c in decision.candidates] == ["ollama"]


# --- overrides de runtime ------------------------------------------------


def test_effective_settings_applies_only_non_null_overrides(monkeypatch):
    from app.llm.registry_routing import effective_settings

    monkeypatch.setattr(
        "app.admin.repository.get_runtime_config",
        lambda: {
            "llm_registry_db": True,
            "llm_provider": None,
            "diagnosis_budget_usd": 2.5,
            "llm_routing_cost_weight": None,
        },
    )
    cfg = _cfg(database_url="postgresql://x/y", llm_registry_db=False, llm_provider="openai")
    eff = effective_settings(cfg)
    assert eff.llm_registry_db is True and eff.diagnosis_budget_usd == 2.5
    assert eff.llm_provider == "openai" and eff.llm_routing_cost_weight == 0.7
    assert effective_settings(_cfg(database_url="")).llm_registry_db is True  # sem banco: intacto


# --- API admin -----------------------------------------------------------


def test_model_create_validates_routing_fields():
    from pydantic import ValidationError

    from app.admin.routes import ModelCreate

    ok = ModelCreate(
        provider_origin="api.groq.com",
        model_id="m",
        domains=["saas", "sap", "sap"],
        transport="openai_compatible",
        tier="standard",
        context_window_tokens=32_000,
    )
    assert ok.domains == ["sap", "saas"] or ok.domains == ["saas", "sap"]
    assert ModelCreate(provider_origin="api.groq.com", model_id="m", domains=[]).domains is None
    for bad in (
        {"transport": "grpc"},
        {"tier": "gold"},
        {"domains": ["hr"]},
        {"context_window_tokens": 10},
    ):
        with pytest.raises(ValidationError):
            ModelCreate(provider_origin="api.groq.com", model_id="m", **bad)


def test_preview_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.llm import gateway
    from app.main import app

    monkeypatch.setattr(settings, "admin_api_key", "admin-test-key-72")
    snap = _snap(_m("qwen", LOCAL), _m("oss", GROQ))
    monkeypatch.setattr(gateway, "load_registry_snapshot", lambda: snap)
    monkeypatch.setattr(gateway, "effective_settings", lambda c: _cfg())
    r = TestClient(app).get(
        "/admin/api/routing/preview?sensitivity=confidential&domain=sap&tokens=2000",
        headers={"X-API-Admin-Key": "admin-test-key-72"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "registry" and body["domain"] == "sap"
    first, second = body["candidates"]
    assert (first["model_id"], first["rank"], first["allowed"]) == ("qwen", 1, True)
    assert second["allowed"] is False and "soberania_bloqueada" in second["reasons"]
    assert second["detail"]
    assert "k" not in str(body.get("weights"))  # sem credencial na resposta


def test_preview_requires_admin_key(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "admin_api_key", "admin-test-key-72")
    assert TestClient(app).get("/admin/api/routing/preview").status_code == 401


# --- migration ------------------------------------------------------------


def test_migration_020_chain_and_model_columns():
    from app.admin.models import LlmCallLatency, LlmModel, LlmRuntimeConfig

    mig = (
        Path(__file__).resolve().parents[1] / "alembic/versions/020_registry_routing.py"
    ).read_text()
    assert 'down_revision: str | Sequence[str] | None = "019"' in mig
    for col in (
        "transport",
        "context_window_tokens",
        "supports_tools",
        "domains",
        "tier",
        "priority",
    ):
        assert col in LlmModel.__table__.columns and f'"{col}"' in mig
    for col in ("diagnosis_budget_usd", "llm_routing_cost_weight", "llm_routing_latency_weight"):
        assert col in LlmRuntimeConfig.__table__.columns and f'"{col}"' in mig
    assert LlmCallLatency.__tablename__ == "llm_call_latency" and '"llm_call_latency"' in mig
