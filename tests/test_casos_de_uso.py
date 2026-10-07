"""Casos de uso executaveis - cada afirmacao de docs/CASOS_DE_USO.md tem um teste aqui.

Validacao 2026-10-07 (Bloco 5, DOC-01): os nove docs/UC_*.md descreviam
funcoes, rotas e numeros que nao existiam no codigo. O documento novo so
afirma o que estes testes observam rodando o pipeline real (grafo LangGraph,
conectores em modo demo, guardrails, evidencia, escalonamento). Duas
substituicoes, e so elas:
  - `retrieve` devolve um trecho do documento de `data/sample_docs` (sem
    Qdrant no CI);
  - `invoke_via_gateway` devolve uma resposta estruturada fixa (sem LLM no
    CI). Nos casos em que o LLM nao deveria ser chamado, o stub FALHA se for.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from app.agent import graph, nodes
from app.agent.prompts import PROMPT_VERSION
from app.agent.state import DiagnosisModel
from app.models import IncidentRequest

DOCS = Path(__file__).resolve().parent.parent / "data" / "sample_docs"


def _hit(source: str) -> dict:
    return {
        "source": source,
        "text": (DOCS / source).read_text(encoding="utf-8")[:800],
        "score": 0.82,
        "rerank_score": 2.0,
        "rerank_score_calibrated": 0.88,
    }


@pytest.fixture
def pipeline(monkeypatch):
    """Configura retrieval e LLM substitutos; devolve um registro das chamadas ao LLM."""
    chamadas: list[str] = []

    def configurar(*, fonte: str | None, llm_cita: str | None = "__fonte__", llm_conf=0.8):
        monkeypatch.setattr(
            nodes,
            "retrieve",
            lambda query, target="incidents", top_k=3: [_hit(fonte)] if fonte else [],
        )

        def gateway(build_and_invoke, state, prompt_text="", model_name=None):
            chamadas.append(prompt_text)
            citada = fonte if llm_cita == "__fonte__" else llm_cita
            modelo = DiagnosisModel(
                probable_root_cause="causa sugerida pelo LLM (stub)",
                confidence=llm_conf,
                next_steps=["passo 1"],
                matched_source=citada,
            )
            return {"messages": [AIMessage(content="ok")], "structured_response": modelo}, "ollama"

        monkeypatch.setattr(nodes, "invoke_via_gateway", gateway)
        monkeypatch.setattr(graph, "record_incident", lambda **kw: None)
        return chamadas

    return configurar


def _evidencias(resposta) -> set[tuple[str, str]]:
    return {(e["source_type"], e["trust_level"]) for e in resposta.model_dump()["evidence"]}


# ---------------------------------------------------------------------------
# UC-01: IDoc em status 51 com conector RFC -> rule engine, sem LLM
# ---------------------------------------------------------------------------


def test_uc01_idoc_51_resolvido_pelo_rule_engine(pipeline):
    chamadas = pipeline(fonte="idoc_status_51.md")
    r = graph.run_diagnosis(
        IncidentRequest(
            description="IDoc travado com status 51",
            interface_type="rfc",
            identifier="RFC-IDOC-51-DEMO",
        )
    )
    assert chamadas == []  # o LLM nao foi chamado
    assert r.agent_domain == "sap"
    assert r.matched_source == "rule_engine:sap_idoc_status_51"
    assert r.llm_provider_used == "rule_engine"
    assert r.model_confidence == pytest.approx(0.9)
    assert r.prompt_version is None and r.prompt_digest is None  # invariante 21
    assert ("rule_engine", "system_observed") in _evidencias(r)
    assert ("connector", "simulated") in _evidencias(r)  # conector em modo demo


# ---------------------------------------------------------------------------
# UC-02: incidente ServiceNow -> dominio saas, LLM, guardrails
# ---------------------------------------------------------------------------


def test_uc02_servicenow_passa_pelo_llm(pipeline):
    chamadas = pipeline(fonte="servicenow_itsm_alert.md", llm_conf=0.8)
    r = graph.run_diagnosis(
        IncidentRequest(
            description="incidente aberto no ServiceNow sobre falha SAP",
            interface_type="servicenow",
            identifier="INC0010001",
        )
    )
    assert len(chamadas) == 1
    assert r.agent_domain == "saas"
    assert r.matched_source == "servicenow_itsm_alert.md"  # citada E recuperada
    assert r.prompt_version == PROMPT_VERSION
    # cenario demo reconhecido: a confianca do LLM so e limitada pela evidencia
    assert r.model_confidence == pytest.approx(0.8)
    assert r.diagnosis_confidence == pytest.approx(0.88 * 0.8, abs=1e-3)
    assert r.escalation["escalation"] == "grounded"


def test_uc02_identificador_desconhecido_limita_a_confianca(pipeline):
    pipeline(fonte="servicenow_itsm_alert.md", llm_conf=0.8)
    r = graph.run_diagnosis(
        IncidentRequest(
            description="incidente aberto no ServiceNow sobre falha SAP",
            interface_type="servicenow",
            identifier="INC9999999",
        )
    )
    # identificador fora dos cenarios: dado de fallback, teto de 0,4
    assert r.model_confidence == pytest.approx(0.4)
    assert r.probable_root_cause.startswith("[confianca limitada")


def test_uc02_fonte_inventada_pelo_llm_e_descartada(pipeline):
    pipeline(fonte="servicenow_itsm_alert.md", llm_cita="documento_que_nao_existe.md")
    r = graph.run_diagnosis(
        IncidentRequest(
            description="incidente aberto no ServiceNow sobre falha SAP",
            interface_type="servicenow",
            identifier="INC0010001",
        )
    )
    assert r.matched_source is None
    assert r.escalation["abstained"] is True and r.escalation["should_escalate"] is True


# ---------------------------------------------------------------------------
# UC-03: texto livre sem conector nem documento -> generic, sem contexto
# ---------------------------------------------------------------------------


def test_uc03_generico_sem_contexto(pipeline):
    pipeline(fonte=None, llm_cita=None, llm_conf=0.7)
    r = graph.run_diagnosis(IncidentRequest(description="erro desconhecido no sistema legado"))
    assert r.agent_domain == "generic"
    assert r.matched_source is None
    assert r.diagnosis_confidence == 0.0
    assert _evidencias(r) == {("user", "user_reported")}
    assert r.escalation["escalation"] == "no_context"


def test_uc03_busca_web_e_fail_closed_sem_fonte_aprovada(monkeypatch):
    monkeypatch.setattr(nodes.settings, "web_search_enabled", True)
    monkeypatch.setattr(nodes.settings, "web_search_policy", "approved")
    monkeypatch.setattr(nodes.settings, "database_url", "")
    assert nodes._web_search_allowed({"interface_type": "odata"}) is False


# ---------------------------------------------------------------------------
# UC-04: catalogo do rule engine
# ---------------------------------------------------------------------------


def test_uc04_catalogo_e_negacao():
    from app.agent.rules import KNOWN_ERROR_RULES, match_known_error

    assert len(KNOWN_ERROR_RULES) == 22
    assert match_known_error("nao e token expirado, a senha foi trocada") is None
    resultado = match_known_error("HTTP 401 Unauthorized")
    assert resultado["rule_engine_category"] == "auth_unauthorized"
    assert resultado["confidence"] == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# UC-05: evidencia fraca -> fallback para a reference_library (desligavel)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ligado", [True, False])
def test_uc05_fallback_da_reference_library(monkeypatch, ligado):
    from app.rag import retriever

    consultou = []

    class _Cliente:
        def get_collection(self, name):
            consultou.append(name)
            return type("I", (), {"points_count": 10})()

    fraco = {"source": "x.md", "text": "t", "score": 0.2}
    forte = {"source": "Manual.pdf", "text": "u", "score": 0.9}
    monkeypatch.setattr(retriever, "_retrieve_hybrid", lambda *a, **k: [fraco])
    monkeypatch.setattr(retriever, "_retrieve_dense_only", lambda *a, **k: [forte])
    monkeypatch.setattr(retriever, "rerank", lambda q, hits, top_k: hits[:top_k])
    monkeypatch.setattr(retriever, "_get_qdrant_client", lambda: _Cliente())
    monkeypatch.setattr(retriever.settings, "reference_library_fallback_enabled", ligado)
    fontes = {h["source"] for h in retriever._retrieve_unified("q", top_k=3, score_threshold=0.5)}
    assert (consultou == ["sap_reference_library"]) is ligado
    assert ("Manual.pdf" in fontes) is ligado


# ---------------------------------------------------------------------------
# UC-07: CloudEvents 1.0 pelo webhook
# ---------------------------------------------------------------------------


def test_uc07_webhook_cloudevents(monkeypatch):
    from fastapi.testclient import TestClient

    from app.events import consumer
    from app.main import app

    monkeypatch.setattr("app.main.settings.event_mesh_api_key", "k" * 32)
    monkeypatch.setattr("app.main.settings.redis_url", "")
    diagnosticos = []
    resposta = SimpleNamespace(diagnosis_confidence=0.9, llm_provider_used="rule_engine")
    monkeypatch.setattr(consumer, "run_diagnosis", lambda req: diagnosticos.append(req) or resposta)
    client = TestClient(app)
    evento = {
        "specversion": "1.0",
        "type": "com.sap.integration.incident.detected.v1",
        "source": "/sap/cpi/monitor",
        "id": "evt-uc07",
        "data": {"description": "IDoc travado com status 51", "interface_type": "rfc"},
    }
    cabecalho = {"X-Event-Mesh-Api-Key": "k" * 32}
    assert client.post("/events/incident", json=evento, headers=cabecalho).status_code == 202
    assert client.post("/events/incident", json=evento, headers=cabecalho).status_code == 202
    assert len(diagnosticos) == 1  # mesmo (source, id): deduplicado
    sem_specversion = {k: v for k, v in evento.items() if k != "specversion"}
    assert (
        client.post("/events/incident", json=sem_specversion, headers=cabecalho).status_code == 422
    )


# ---------------------------------------------------------------------------
# UC-08: GraphRAG muda a forma do grafo (decidido na construcao)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ligado", [False, True])
def test_uc08_graphrag_muda_o_grafo(monkeypatch, ligado):
    monkeypatch.setattr(graph.settings, "graph_rag_enabled", ligado)
    nos = set(graph.build_graph().get_graph().nodes)
    assert ({"graph_enrich", "graph_write"} <= nos) is ligado
