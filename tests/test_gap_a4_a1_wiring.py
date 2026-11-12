"""Gap A4 (DA-64) e A1 do estudo IIC -> SAP AMS/DF-e.

A4: o ontology_enrich_node precisa rodar ENTRE o diagnostico e o report, e
as chaves que ele devolve precisam estar no CopilotState (senao o StateGraph
as descarta). A1: o conector OData filtra por MessageGuid e le a mensagem de
forma tolerante (StatusText -> LogText -> CustomStatus).
"""

import httpx
import pytest

from app.agent import graph as graph_mod
from app.agent.state import CopilotState
from app.config import Settings
from app.connectors.odata_connector import ODataConnector


def _edges(compiled) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in compiled.get_graph().edges}


@pytest.mark.parametrize("graph_rag", [False, True])
def test_ontology_roda_antes_do_report(monkeypatch, graph_rag):
    monkeypatch.setattr(graph_mod.settings, "ontology_enrichment_enabled", True)
    monkeypatch.setattr(graph_mod.settings, "graph_rag_enabled", graph_rag)
    edges = _edges(graph_mod.build_graph())
    assert ("ontology_enrich", "report") in edges
    assert ("report", "ontology_enrich") not in edges
    upstream = "graph_write" if graph_rag else "sap_diagnose"
    assert (upstream, "ontology_enrich") in edges


def test_sem_ontology_grafo_inalterado(monkeypatch):
    monkeypatch.setattr(graph_mod.settings, "ontology_enrichment_enabled", False)
    monkeypatch.setattr(graph_mod.settings, "graph_rag_enabled", False)
    edges = _edges(graph_mod.build_graph())
    assert ("sap_diagnose", "report") in edges
    assert not any("ontology" in a or "ontology" in b for a, b in edges)


def test_state_declara_chaves_da_ontologia():
    keys = CopilotState.__annotations__
    assert "ontology_inference" in keys and "ontology_next_steps" in keys


def _odata(monkeypatch, record: dict):
    monkeypatch.setattr(
        "app.connectors.odata_connector.settings",
        Settings(
            odata_service_url="https://tenant.cpi.example.com/api/v1/MessageProcessingLogs",
            odata_oauth_token_url="https://tenant.authentication.example.com/oauth/token",
            odata_client_id="client-id",
            odata_client_secret="client-secret",
        ),
    )
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "fake-token"})
        seen["filter"] = request.url.params["$filter"]
        return httpx.Response(200, json={"d": {"results": [record]}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ODataConnector(client=client).fetch("GUID-1"), seen


def test_odata_filtra_por_message_guid_e_le_custom_status(monkeypatch):
    result, seen = _odata(
        monkeypatch, {"MessageGuid": "GUID-1", "Status": "FAILED", "CustomStatus": "HTTP 503"}
    )
    assert seen["filter"] == "MessageGuid eq 'GUID-1'"
    assert result.status == "error"
    assert result.error_code == "FAILED"
    assert result.message == "HTTP 503"
