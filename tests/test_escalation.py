"""Testes da DA-44 (sinal de escalonamento em tres tiers).

O teste central nao e' "o sinal escalona quando deve". E' o
CONTRAPOSITIVO: `_compute_evidence_strength()` tem piso 0.75 quando o
conector e' real, entao um gatilho baseado nele nunca dispararia nesse
caminho. Aqui se prova que o sinal novo NAO tem essa cegueira.
"""

import dataclasses
from types import SimpleNamespace

import pytest

from app.agent.escalation import (
    CURATED_TIER_MIN_EVIDENCE,
    FLOOR_COLLECTION,
    FLOOR_TIER_MIN_EVIDENCE,
    Reason,
    compute_escalation_signal,
)


def conn(*, mock=False, fallback=False):
    return SimpleNamespace(is_mock=mock, is_fallback=fallback)


def hit(collection, evidence, source="doc.md"):
    return {
        "collection": collection,
        "rerank_score_calibrated": evidence,
        "score": evidence,
        "source": source,
        "text": "conteudo",
    }


def state(hits=(), data=None, diagnosis=None):
    return {
        "retrieved_context": list(hits),
        "connector_data": data,
        "diagnosis": diagnosis if diagnosis is not None else {"matched_source": "doc.md"},
    }


# --- a propriedade que motivou a DA ------------------------------------------


def test_escala_mesmo_com_conector_real_onde_evidence_strength_satura():
    """O piso de 0.75 de _compute_evidence_strength (nodes.py:581) faz
    evidence_strength >= 0.75 sempre com conector real. Um gatilho
    baseado nele nunca dispararia aqui. Este sinal escala mesmo assim,
    porque pergunta outra coisa."""
    s = state(
        hits=[hit("sap_incident_docs", 0.30)],
        data=conn(),  # real: nao mock, nao fallback
        diagnosis={"matched_source": None},  # guardrail anulou
    )
    d = compute_escalation_signal(s)

    # o piso de evidence_strength seguraria qualquer limiar acima de 0.75
    assert d.connector_real is True
    assert d.should_escalate is True
    assert d.reason == Reason.ABSTAINED
    # e o sinal nao carrega o mesmo piso
    assert d.top_evidence == pytest.approx(0.30)


def test_conector_real_com_evidencia_boa_nao_escala():
    s = state(
        hits=[hit("sap_incident_docs", 0.88)],
        data=conn(),
        diagnosis={"matched_source": "doc.md"},
    )
    d = compute_escalation_signal(s)
    assert d.should_escalate is False
    assert d.reason == Reason.GROUNDED


# --- abstencao: o sinal mais forte -------------------------------------------


def test_abstencao_escala_mesmo_com_evidencia_alta():
    """matched_source None e' o guardrail dizendo "nao achei lastro". Um
    score alto de retrieval nao conserta isso - o documento pode ser
    irrelevante, que e' precisamente o que o guardrail detectou."""
    s = state(
        hits=[hit("sap_incident_docs", 0.95)],
        data=conn(mock=True),
        diagnosis={"matched_source": None},
    )
    d = compute_escalation_signal(s)
    assert d.should_escalate is True
    assert d.reason == Reason.ABSTAINED
    assert d.abstained is True


def test_abstaincao_tem_precedencia_sobre_tier_fraco():
    """Regra 1 vence a regra 2: o codigo de razao precisa ser unico e
    diagnostico, nao uma combinacao ambigua."""
    s = state(
        hits=[hit(FLOOR_COLLECTION, 0.10)],
        data=conn(),
        diagnosis={"matched_source": None},
    )
    assert compute_escalation_signal(s).reason == Reason.ABSTAINED


# --- tier de piso: o defeito de escala --------------------------------------


def test_tier_de_piso_fraco_escala():
    s = state(hits=[hit(FLOOR_COLLECTION, FLOOR_TIER_MIN_EVIDENCE - 0.01)], data=conn())
    d = compute_escalation_signal(s)
    assert d.should_escalate is True
    assert d.reason == Reason.FLOOR_TIER_WEAK
    assert d.tier == "floor"


def test_tier_de_piso_forte_nao_escala():
    s = state(hits=[hit(FLOOR_COLLECTION, 0.88)], data=conn())
    assert compute_escalation_signal(s).should_escalate is False


def test_mesma_escala_nao_implica_mesma_forca_entre_tiers():
    """O ponto da DA. Mesma pontuacao de reranker, tiers de peso
    probatorio diferente: 0.55 no tier de piso e' suspeito, no curado
    nao. Sem tier no sinal, um limiar unico seria arbitrario."""
    fraco_piso = compute_escalation_signal(state(hits=[hit(FLOOR_COLLECTION, 0.55)], data=conn()))
    ok_curado = compute_escalation_signal(state(hits=[hit("sap_incident_docs", 0.55)], data=conn()))
    assert fraco_piso.should_escalate is True
    assert ok_curado.should_escalate is False


# --- sem contexto: decisao contrariante e' o ponto ---------------------------


def test_sem_contexto_nao_escala():
    """Modelo maior nao cria evidencia. Sem chunk e sem conector, o tier 3
    produziria alucinao mais confiante - pior para o engenheiro de
    suporte do que 'dados insuficientes'."""
    d = compute_escalation_signal(state())
    assert d.should_escalate is False
    assert d.reason == Reason.NO_CONTEXT
    assert d.tier == "none"


def test_sem_contexto_ganha_de_evidencia_fraco():
    """Ordem das regras: ausencia de contexto e' explicita e nao pode ser
    mascarada por um tier fraco qualquer."""
    assert (
        compute_escalation_signal(state()).reason
        != compute_escalation_signal(state(hits=[hit(FLOOR_COLLECTION, 0.01)])).reason
    )


# --- tier curado fraco -------------------------------------------------------


def test_curado_fraco_escala():
    s = state(
        hits=[hit("sap_incident_docs", CURATED_TIER_MIN_EVIDENCE - 0.01)],
        data=conn(),
    )
    d = compute_escalation_signal(s)
    assert d.should_escalate is True
    assert d.reason == Reason.CURATED_TIER_WEAK


def test_hit_sem_collection_usa_piso_curado():
    """Legado/collection ausente nao pode virar tier de piso: o default
    seguro e' o mais próximo de 'documento curado'."""
    s = state(hits=[{"rerank_score_calibrated": 0.10, "source": "x.md", "text": "t"}], data=conn())
    assert compute_escalation_signal(s).reason == Reason.CURATED_TIER_WEAK


def test_hit_sem_evidence_tratado_como_zero():
    """Sem rerank_score_calibrated, ler 'score' seria reintroduzir
    justamente a escala nao calibrada que a DA veio remover."""
    s = state(hits=[{"collection": "sap_incident_docs", "score": 0.99}], data=conn())
    d = compute_escalation_signal(s)
    assert d.top_evidence == 0.0
    assert d.should_escalate is True


# --- invariantes da DA --------------------------------------------------------


def test_determinismo():
    s = state(hits=[hit(FLOOR_COLLECTION, 0.20)], data=conn(), diagnosis={"matched_source": None})
    a, b = compute_escalation_signal(s), compute_escalation_signal(s)
    assert a == b


def test_decisao_imutavel():
    s = state(hits=[hit(FLOOR_COLLECTION, 0.1)], data=conn())
    d = compute_escalation_signal(s)
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.should_escalate = True  # type: ignore[misc]


def test_modulo_nao_conhece_provider_pago():
    """O destino do escalonamento e' decisao do AI Gateway (DA-26), que
    aplica a politica de DA-43. Se este modulo citasse um provider, a
    politica poderia ser contornada por ele."""
    s = state(hits=[hit(FLOOR_COLLECTION, 0.1)], data=conn())
    d = compute_escalation_signal(s)
    assert d.escalate_to == "cloud_premium"
    assert d.escalate_to not in {"openai", "anthropic", "gemini", "azure"}


def test_log_nao_vaza_conteudo():
    """Auditoria precisa do veredito, nunca do documento."""
    s = state(
        hits=[hit(FLOOR_COLLECTION, 0.1, source="segredo-financeiro.pdf")],
        data=conn(),
    )
    fields = compute_escalation_signal(s).as_log_fields()
    assert "segredo-financeiro" not in str(fields)
    assert set(fields) == {
        "escalation",
        "should_escalate",
        "tier",
        "abstained",
        "top_evidence",
        "connector_real",
    }


def test_razao_sempre_conhecida():
    """Codigo de razao e' contrato para o gateway e o log. Texto livre
    aqui quebra o parsing downstream."""
    cknown = {
        Reason.GROUNDED,
        Reason.NO_CONTEXT,
        Reason.ABSTAINED,
        Reason.FLOOR_TIER_WEAK,
        Reason.CURATED_TIER_WEAK,
    }
    for hits, data, diag in [
        ([], None, {"matched_source": None}),
        ([hit(FLOOR_COLLECTION, 0.1)], conn(), {"matched_source": None}),
        ([hit(FLOOR_COLLECTION, 0.1)], conn(), {}),
        ([hit("sap_incident_docs", 0.9)], conn(), {"matched_source": "d.md"}),
    ]:
        assert compute_escalation_signal(state(hits, data, diag)).reason in cknown
