"""DA-61 Fase 2: ontology_enrich_node deve degradar graciosamente quando
error_codes.ttl falha (nao encontrado, parse error) ou ontology enrichment
nao esta ativado (ONTOLOGY_ENRICHMENT_ENABLED=false). Nunca deve derrubar
o diagnostico inteiro."""

from app.agent import nodes as nodes_module
from app.agent.nodes import ontology_enrich_node
from app.connectors.base import ConnectorResult


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


def test_retrieve_node_with_ontology_enabled_accepts_connector_result(monkeypatch):
    """IIC-AUD-301: `connector_data` e um `ConnectorResult` (attrs), nao dict.

    Antes do fix, `state["connector_data"].get(...)` levantava AttributeError
    e derrubava qualquer requisicao com a flag ligada.
    """
    monkeypatch.setattr(nodes_module.settings, "ontology_enrichment_enabled", True)
    monkeypatch.setattr(
        nodes_module,
        "get_upper_categories_from_category",
        lambda category: ["http://example.org/iic/error_codes#AuthenticationError"],
    )

    capturado: dict[str, str] = {}

    def _retrieve_ontology(query: str, *, target: str, top_k: int, ontology_category: str):
        capturado["categoria"] = ontology_category
        return [{"doc": "ontology-aware"}]

    monkeypatch.setattr(nodes_module, "retrieve_hybrid_ontology_aware", _retrieve_ontology)
    monkeypatch.setattr(nodes_module, "retrieve", lambda *a, **k: [{"doc": "plain"}])

    connector_data = ConnectorResult(
        source_system="odata",
        status="error",
        error_code="HTTP401Unauthorized",
        message="401",
        is_mock=True,
        raw={},
    )

    result = nodes_module.retrieve_node({"description": "x", "connector_data": connector_data})

    assert result["retrieved_context"] == [{"doc": "ontology-aware"}]
    assert capturado["categoria"] == "http://example.org/iic/error_codes#AuthenticationError"
