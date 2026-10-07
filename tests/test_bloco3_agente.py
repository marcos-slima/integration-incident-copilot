"""Validacao 2026-10-07, Bloco 3: pipeline do agente (M-02, M-03, M-04, M-26)."""

from __future__ import annotations

import pytest

from app.agent.rules import match_known_error

# ---------------------------------------------------------------------------
# M-02: rule engine - negacao, janela curta, palavras inteiras
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "Nao e problema de token expirado; o pedido ficou travado na fila",
        "Não é token expirado, o client foi renovado ontem",
        "This is not a token expired issue, the payload is malformed",
        "Ja verificamos a janela de manutencao e o pedido existe no SAP",
        "o cliente da loja existe no CRM",
        "a autorizacao do fornecedor saiu. Depois o processo falhou em outra etapa",
    ],
)
def test_m02_sem_falso_positivo(texto):
    assert match_known_error(texto) is None


@pytest.mark.parametrize(
    ("texto", "categoria"),
    [
        ("token OAuth expirado ao chamar o SuccessFactors", "auth_oauth_expired"),
        ("HTTP 401 Unauthorized ao chamar o OData", "auth_unauthorized"),
        ("documento ja existe no FI", "duplicate_document"),
        ("IDoc travado com status 51 no sistema de destino", "sap_idoc_status_51"),
        ("o certificado SSL expirou ontem", "ssl_certificate_expired"),
        ("Sistema sem resposta: connection timeout", "http_timeout"),
    ],
)
def test_m02_casos_reais_continuam(texto, categoria):
    assert match_known_error(texto)["rule_engine_category"] == categoria


def test_m02_negacao_so_descarta_aquele_trecho():
    """A negacao descarta o casamento negado; outro erro real no texto continua valendo."""
    r = match_known_error("Nao e token expirado. O log mostra connection refused no destino")
    assert r["rule_engine_category"] == "network_connection_refused"


# ---------------------------------------------------------------------------
# M-03: confianca do rule engine nao e zerada pelo guardrail
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("com_conector", "forca"), [(False, 0.70), (True, 0.95)])
def test_m03_regra_mantem_a_propria_evidencia(com_conector, forca):
    from app.agent.nodes import _apply_confidence_guardrails

    regra = match_known_error("IDoc status 51 no envio", has_connector_data=com_conector)
    out = _apply_confidence_guardrails(dict(regra), {"retrieved_context": []})
    assert out["evidence_strength"] == forca
    assert out["matched_source"] == "rule_engine:sap_idoc_status_51"
    assert out["model_confidence"] == 0.90
    assert out["diagnosis_confidence"] == round(forca * 0.90, 3)
    assert not out["probable_root_cause"].startswith("[confianca limitada")


# ---------------------------------------------------------------------------
# M-04: matched_source inventado e descartado mesmo sem RAG
# ---------------------------------------------------------------------------


def test_m04_fonte_inventada_com_conector_real_e_sem_rag():
    from app.agent.nodes import _apply_confidence_guardrails
    from app.connectors.base import ConnectorResult

    cr = ConnectorResult(
        source_system="OData", status="error", error_code="401", message="m", raw="r", is_mock=False
    )
    out = _apply_confidence_guardrails(
        {"matched_source": "documento_inventado.md", "probable_root_cause": "x", "confidence": 0.9},
        {"connector_data": cr, "retrieved_context": []},
    )
    assert out["matched_source"] is None
    assert out["model_confidence"] <= 0.3


def test_m04_fonte_recuperada_continua_valida():
    from app.agent.nodes import _apply_confidence_guardrails

    out = _apply_confidence_guardrails(
        {"matched_source": "cpi_http_401.md", "probable_root_cause": "x", "confidence": 0.9},
        {"retrieved_context": [{"source": "cpi_http_401.md", "score": 0.8, "text": "t"}]},
    )
    assert out["matched_source"] == "cpi_http_401.md"


def test_m02_401_sem_texto_de_erro_segue_para_rag():
    """O texto "erro 401" sozinho nao vira regra: o caso tem documento curado
    (cpi_http_401.md) e continua indo para RAG + LLM, como antes."""
    assert match_known_error("iFlow falhando com erro 401") is None


# ---------------------------------------------------------------------------
# M-26: sinal de escalonamento calculado e devolvido
# ---------------------------------------------------------------------------


def test_m26_resposta_traz_o_sinal_de_escalonamento(monkeypatch):
    from app.agent import graph
    from app.models import IncidentRequest

    final = {
        "diagnosis": {
            "probable_root_cause": "x",
            "model_confidence": 0.2,
            "diagnosis_confidence": 0.0,
            "next_steps": [],
            "matched_source": None,
        },
        "retrieved_context": [],
        "report_markdown": "",
    }
    monkeypatch.setattr(graph, "_invoke_graph_with_timeout", lambda state: {**state, **final})
    monkeypatch.setattr(graph, "record_incident", lambda **kw: None)
    resp = graph.run_diagnosis(IncidentRequest(description="algo estranho aconteceu"))
    assert resp.escalation is not None
    assert resp.escalation["escalation"] == "no_context"
    assert resp.escalation["should_escalate"] is False
