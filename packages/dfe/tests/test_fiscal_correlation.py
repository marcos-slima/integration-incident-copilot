"""DA-93 fase 3: linha do tempo e regras fiscais (app/fiscal/correlation.py)."""

from __future__ import annotations

import copy

import pytest
from dfe_copilot.fiscal import (
    FiscalCorrelationError,
    SchemaPackage,
    analyze_fiscal_xml,
    diagnose_duplicity,
    diagnose_timeout,
    normalize_events,
)
from dfe_copilot.fiscal.xml_tools import NS

KEY = "35261111222333000181550010000000011000000014"
OTHER = "35261111222333000181550010000000021000000011"

PROT_204 = (
    f'<protNFe xmlns="{NS}" versao="4.00"><infProt><tpAmb>2</tpAmb><chNFe>{KEY}</chNFe>'
    "<cStat>204</cStat><xMotivo>Rejeicao: Duplicidade de NF-e</xMotivo></infProt></protNFe>"
).encode()

EVENTS = [
    {"evidence_id": "EV-A", "document_ref": KEY, "request_id": "REQ-A",
     "at": "2026-11-11T09:59:00-03:00", "event": "sent"},
    {"evidence_id": "EV-T", "document_ref": KEY, "request_id": "REQ-A",
     "at": "2026-11-11T13:00:00Z", "event": "timeout"},
    {"evidence_id": "EV-B", "document_ref": KEY, "request_id": "REQ-B",
     "at": "2026-11-11T13:00:01Z", "event": "sent"},
]  # fmt: skip


@pytest.fixture(scope="module")
def facts() -> dict:
    return analyze_fiscal_xml(PROT_204, SchemaPackage.load())


def _ids(result: dict) -> list[str]:
    return [f["id"] for f in result["findings"]]


class TestLinhaDoTempo:
    def test_normaliza_para_utc_e_ordena(self) -> None:
        tl = normalize_events(list(reversed(EVENTS)))
        assert [e.evidence_id for e in tl] == ["EV-A", "EV-T", "EV-B"]
        assert all(e.at.utcoffset().total_seconds() == 0 for e in tl)  # type: ignore[union-attr]

    @pytest.mark.parametrize(
        ("patch", "code"),
        [({"at": "2026-11-11T13:00:00"}, "naive_timestamp"), ({"event": "retry"}, "unknown_event"),
         ({"document_ref": ""}, "missing_field")],
    )  # fmt: skip
    def test_evento_invalido_recusado(self, patch: dict, code: str) -> None:
        with pytest.raises(FiscalCorrelationError) as exc:
            normalize_events([{**EVENTS[0], **patch}])
        assert exc.value.code == code


class TestDuplicidade204:
    def test_h1_com_reenvio_apos_timeout(self, facts: dict) -> None:
        r = diagnose_duplicity(facts, normalize_events(EVENTS))
        assert _ids(r) == ["F-204", "H1"]
        assert r["cause_status"] == "hypothesis"
        assert r["resolution_status"] == "not_validated"
        assert r["remote_authorization_confirmed"] is False
        assert r["automatic_actions"] == []
        assert "nao reenviar" in r["next_step"]

    def test_sem_timeout_sem_h1(self, facts: dict) -> None:
        r = diagnose_duplicity(
            facts, normalize_events([e for e in EVENTS if e["event"] != "timeout"])
        )
        assert _ids(r) == ["F-204", "G-204"] and r["cause_status"] == "inconclusive"

    def test_timeout_depois_do_segundo_envio_sem_h1(self, facts: dict) -> None:
        late = copy.deepcopy(EVENTS)
        late[1]["at"] = "2026-11-11T13:05:00Z"
        assert "H1" not in _ids(diagnose_duplicity(facts, normalize_events(late)))

    def test_documentos_distintos_sem_h1(self, facts: dict) -> None:
        distinct = copy.deepcopy(EVENTS)
        distinct[2]["document_ref"] = OTHER
        assert "H1" not in _ids(diagnose_duplicity(facts, normalize_events(distinct)))

    def test_outro_cstat_nao_aplica_regra(self, facts: dict) -> None:
        r = diagnose_duplicity({**facts, "document_cStat": "100"}, normalize_events(EVENTS))
        assert r["findings"] == []

    def test_chave_com_dv_invalido_nao_aplica_regra(self, facts: dict) -> None:
        r = diagnose_duplicity({**facts, "access_key_dv_valid": False}, normalize_events(EVENTS))
        assert r["findings"] == []

    def test_status_de_lote_nao_dispara(self) -> None:
        lote = f'<retEnviNFe xmlns="{NS}" versao="4.00"><cStat>204</cStat></retEnviNFe>'.encode()
        r = diagnose_duplicity(analyze_fiscal_xml(lote, None), normalize_events(EVENTS))
        assert r["findings"] == []


class TestTimeout:
    def test_sem_resposta_resultado_desconhecido(self) -> None:
        r = diagnose_timeout(KEY, normalize_events(EVENTS[:2]))
        assert r["processing_outcome"] == "unknown"
        assert _ids(r) == ["F-timeout", "H-timeout"]
        assert "indisponi" not in " ".join(f["statement"] for f in r["findings"]).lower()
        assert r["automatic_actions"] == []

    def test_com_resposta_posterior(self) -> None:
        resp = {
            **EVENTS[0],
            "evidence_id": "EV-R",
            "at": "2026-11-11T13:00:30Z",
            "event": "response",
        }
        r = diagnose_timeout(KEY, normalize_events([*EVENTS[:2], resp]))
        assert r["processing_outcome"] == "response_received"
        assert "H-timeout" not in _ids(r)

    def test_resposta_anterior_ao_timeout_nao_conta(self) -> None:
        resp = {
            **EVENTS[0],
            "evidence_id": "EV-R",
            "at": "2026-11-11T12:59:30Z",
            "event": "response",
        }
        r = diagnose_timeout(KEY, normalize_events([*EVENTS[:2], resp]))
        assert r["processing_outcome"] == "unknown"

    def test_sem_timeout_nao_aplica(self) -> None:
        r = diagnose_timeout(KEY, normalize_events([EVENTS[0]]))
        assert r["processing_outcome"] == "not_applicable" and r["findings"] == []

    def test_timeout_de_outro_documento_ignorado(self) -> None:
        r = diagnose_timeout(OTHER, normalize_events(EVENTS))
        assert r["processing_outcome"] == "not_applicable"
