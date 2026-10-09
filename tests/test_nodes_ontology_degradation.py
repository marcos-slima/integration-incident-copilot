"""DA-61 Fase 2: ontology_enrich_node deve degradar graciosamente quando
error_codes.ttl falha (nao encontrado, parse error) ou ontology enrichment
nao esta ativado (ONTOLOGY_ENRICHMENT_ENABLED=false). Nunca deve derrubar
o diagnostico inteiro."""

from app.agent.nodes import ontology_enrich_node


def test_ontology_enrich_node_returns_empty_when_no_category():
    """Node com estado sem rule_engine_category deve devolver estruturas vazias."""
    result = ontology_enrich_node({"diagnosis": {}})
    assert result == {"ontology_inference": "", "ontology_next_steps": ""}


def test_ontology_enrich_node_degrades_gracefully_on_ttl_parse_error(monkeypatch):
    """Parse error no TTL (syntatically invalid) → log warning + estruturas vazias."""
    monkeypatch.setattr(
        "app.ontology.enrichment.get_upper_categories_from_category", lambda *a, **k: []
    )
    monkeypatch.setattr("app.ontology.enrichment.get_next_steps_from_category", lambda *a, **k: [])

    def _boom(*_args, **_kwargs):
        raise ValueError("invalid TTL syntax")

    monkeypatch.setattr("app.ontology.enrichment.get_upper_categories_from_category", _boom)
    monkeypatch.setattr("app.ontology.enrichment.get_next_steps_from_category", _boom)

    result = ontology_enrich_node({"diagnosis": {"rule_engine_category": "HTTP401Unauthorized"}})
    assert result == {"ontology_inference": "", "ontology_next_steps": ""}


def test_ontology_enrich_node_success_with_mocked_ttl(monkeypatch):
    """Quando TTL carrega com sucesso, devolve upper categories e next steps
    formatados como graph context (string) e next steps (newline separated)."""
    monkeypatch.setattr(
        "app.ontology.enrichment.get_upper_categories_from_category",
        lambda c: ["AuthenticationError"],
    )
    monkeypatch.setattr(
        "app.ontology.enrichment.get_next_steps_from_category",
        lambda c: ["Refresh token", "Re-authenticate"],
    )

    result = ontology_enrich_node({"diagnosis": {"rule_engine_category": "HTTP401Unauthorized"}})

    assert "AuthenticationError" in result["ontology_inference"]
    assert "- Refresh token" in result["ontology_next_steps"]
    assert "- Re-authenticate" in result["ontology_next_steps"]


def test_ontology_enrich_node_next_steps_empty_when_not_defined(monkeypatch):
    """Se next steps nao estiver definido no TTL para a categoria, devolve
    "- Nenhum next step definido"."""
    monkeypatch.setattr("app.ontology.enrichment.get_upper_categories_from_category", lambda c: [])
    monkeypatch.setattr("app.ontology.enrichment.get_next_steps_from_category", lambda c: [])

    result = ontology_enrich_node({"diagnosis": {"rule_engine_category": "HTTP401Unauthorized"}})

    assert result["ontology_next_steps"] == "- Nenhum next step definido"
