"""DA-25 (Evidence/Trust Layer): _assemble_evidence monta a lista de
evidencias de forma inteiramente DETERMINISTICA a partir do que o
pipeline realmente consultou (state) - nunca a partir de autoavaliacao
ou citacao do LLM. Estes testes cobrem cada fonte isoladamente e a
combinacao delas, alem da validacao do modelo Evidence/DiagnosisResponse
(app/models.py)."""

from app.agent.nodes import _assemble_evidence, report_node
from app.connectors.base import ConnectorResult
from app.models import DiagnosisResponse, Evidence
from app.rag.graph_store import RelatedIncident


def test_no_sources_yields_empty_evidence_when_description_missing():
    assert _assemble_evidence({}) == []


def test_user_description_is_always_included_as_user_reported():
    evidence = _assemble_evidence({"description": "iFlow falhando com timeout"})
    assert len(evidence) == 1
    item = evidence[0]
    assert item["source_type"] == "user"
    assert item["trust_level"] == "user_reported"
    assert item["excerpt"] == "iFlow falhando com timeout"


def test_real_connector_data_is_system_observed():
    data = ConnectorResult(
        source_system="OData",
        status="error",
        error_code="401",
        message="Unauthorized",
        raw="{}",
        is_mock=False,
        is_fallback=False,
    )
    evidence = _assemble_evidence({"connector_data": data, "description": "erro 401"})
    connector_items = [e for e in evidence if e["source_type"] == "connector"]
    assert len(connector_items) == 1
    assert connector_items[0]["trust_level"] == "system_observed"
    assert connector_items[0]["source_id"] == "connector:OData"


def test_mock_or_fallback_connector_data_is_simulated_not_system_observed():
    mock_data = ConnectorResult(
        source_system="RFC",
        status="error",
        error_code=None,
        message="mock generico",
        raw="{}",
        is_mock=True,
        is_fallback=False,
    )
    evidence = _assemble_evidence({"connector_data": mock_data, "description": "x"})
    connector_items = [e for e in evidence if e["source_type"] == "connector"]
    assert connector_items[0]["trust_level"] == "simulated"

    fallback_data = ConnectorResult(
        source_system="RFC",
        status="error",
        error_code=None,
        message="fallback generico",
        raw="{}",
        is_mock=False,
        is_fallback=True,
    )
    evidence = _assemble_evidence({"connector_data": fallback_data, "description": "x"})
    connector_items = [e for e in evidence if e["source_type"] == "connector"]
    assert connector_items[0]["trust_level"] == "simulated"


def test_rag_hits_become_retrieved_document_evidence_with_scores():
    hits = [
        {
            "source": "cpi_http_401.md",
            "text": "conteudo do chunk",
            "score": 0.82,
            "rerank_score": 7.1,
        },
        {"source": "outro.md", "text": "outro chunk", "score": 0.55},
    ]
    evidence = _assemble_evidence({"retrieved_context": hits, "description": "x"})
    rag_items = [e for e in evidence if e["source_type"] == "rag"]

    assert len(rag_items) == 2
    assert rag_items[0]["locator"] == "cpi_http_401.md"
    assert rag_items[0]["retrieval_score"] == 0.82
    assert rag_items[0]["rerank_score"] == 7.1
    assert rag_items[0]["trust_level"] == "retrieved_document"
    # segundo hit nao tem rerank_score - deve vir None, nao KeyError
    assert rag_items[1]["rerank_score"] is None


def test_graph_history_becomes_graph_evidence():
    related = RelatedIncident(
        interface_identifier="ID-1",
        source_system="OData",
        root_cause="token expirado",
        matched_document="cpi_http_401.md",
        evidence_strength=0.7,
        is_grounded=True,
    )
    evidence = _assemble_evidence({"graph_history": [related], "description": "x"})
    graph_items = [e for e in evidence if e["source_type"] == "graph"]

    assert len(graph_items) == 1
    assert graph_items[0]["locator"] == "cpi_http_401.md"
    assert graph_items[0]["excerpt"] == "token expirado"
    assert graph_items[0]["retrieval_score"] == 0.7
    assert graph_items[0]["trust_level"] == "retrieved_document"


def test_web_search_results_become_web_untrusted_evidence():
    web_results = [{"source": "web_search", "text": "resultado da busca", "score": 0.0}]
    evidence = _assemble_evidence({"web_search_results": web_results, "description": "x"})
    web_items = [e for e in evidence if e["source_type"] == "web"]

    assert len(web_items) == 1
    assert web_items[0]["trust_level"] == "web_untrusted"


def test_web_search_error_result_is_not_treated_as_evidence():
    web_results = [{"source": "web_search_error", "text": "erro de rede", "score": 0.0}]
    evidence = _assemble_evidence({"web_search_results": web_results, "description": "x"})
    assert [e for e in evidence if e["source_type"] == "web"] == []


def test_all_sources_combine_in_one_evidence_list():
    data = ConnectorResult(
        source_system="OData",
        status="error",
        error_code="401",
        message="Unauthorized",
        raw="{}",
        is_mock=False,
        is_fallback=False,
    )
    hits = [{"source": "cpi_http_401.md", "text": "chunk", "score": 0.9}]
    related = RelatedIncident(
        interface_identifier="ID-1",
        source_system="OData",
        root_cause="causa",
        matched_document="cpi_http_401.md",
        evidence_strength=0.6,
        is_grounded=True,
    )
    web_results = [{"source": "web_search", "text": "web", "score": 0.0}]

    evidence = _assemble_evidence(
        {
            "description": "erro 401 no iFlow",
            "connector_data": data,
            "retrieved_context": hits,
            "graph_history": [related],
            "web_search_results": web_results,
        }
    )

    source_types = [e["source_type"] for e in evidence]
    assert source_types == ["connector", "rag", "graph", "web", "user"]


def test_evidence_model_validates_assembled_dicts():
    """Cada dict montado por _assemble_evidence precisa validar como
    Evidence - garante que o contrato entre nodes.py e models.py nao
    se desalinha silenciosamente."""
    hits = [{"source": "doc.md", "text": "x", "score": 0.5, "rerank_score": 1.2}]
    evidence = _assemble_evidence({"retrieved_context": hits, "description": "incidente"})
    for item in evidence:
        Evidence.model_validate(item)


def test_diagnosis_response_defaults_evidence_to_empty_list():
    response = DiagnosisResponse(
        probable_root_cause="causa",
        confidence=0.5,
        next_steps=[],
        report_markdown="",
    )
    assert response.evidence == []


def test_diagnosis_response_accepts_evidence_list():
    response = DiagnosisResponse(
        probable_root_cause="causa",
        confidence=0.5,
        next_steps=[],
        report_markdown="",
        evidence=[
            {
                "source_id": "user:description",
                "source_type": "user",
                "excerpt": "incidente",
                "trust_level": "user_reported",
            }
        ],
    )
    assert len(response.evidence) == 1
    assert response.evidence[0].source_type == "user"


def test_report_markdown_includes_evidence_section():
    state = {
        "description": "erro 401 no iFlow",
        "diagnosis": {
            "probable_root_cause": "token expirado",
            "confidence": 0.8,
            "next_steps": ["renovar token"],
            "matched_source": "cpi_http_401.md",
            "evidence_strength": 0.7,
        },
        "retrieved_context": [{"source": "cpi_http_401.md", "text": "chunk", "score": 0.9}],
    }
    result = report_node(state)
    markdown = result["report_markdown"]

    assert "Evidencias" in markdown
    assert "[retrieved_document] rag (cpi_http_401.md)" in markdown
    assert "[user_reported] user" in markdown
