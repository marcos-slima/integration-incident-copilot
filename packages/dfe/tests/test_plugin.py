"""Plugin DF-e sobre o core (DA-94): contexto, findings e integracao com o grafo."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest
from dfe_copilot.context import FiscalContext
from dfe_copilot.fiscal.xml_tools import NS
from dfe_copilot.plugin import PLUGIN
from pydantic import ValidationError

from app import sdk
from app.sdk.testing import diagnosis_harness

KEY = "35261111222333000181550010000000011000000014"
PROT_204 = (
    f'<protNFe xmlns="{NS}" versao="4.00"><infProt><tpAmb>2</tpAmb>'
    f"<verAplic>DEMO_IIC</verAplic><chNFe>{KEY}</chNFe>"
    "<dhRecbto>2026-11-11T09:52:00-03:00</dhRecbto>"
    "<cStat>204</cStat><xMotivo>Rejeicao: Duplicidade de NF-e</xMotivo></infProt></protNFe>"
)
EVENTS = [
    {"evidence_id": "EV-A", "document_ref": KEY, "at": "2026-11-11T12:59:00Z", "event": "sent"},
    {"evidence_id": "EV-T", "document_ref": KEY, "at": "2026-11-11T13:00:00Z", "event": "timeout"},
    {"evidence_id": "EV-B", "document_ref": KEY, "at": "2026-11-11T13:00:01Z", "event": "sent"},
]
CTX = {"environment": "homologation", "jurisdiction": "SP", "events": EVENTS}
POSITIVO = (Path(__file__).parent / "fixtures" / "nfe_controle_positivo_sp.xml").read_text(
    encoding="utf-8"
)


def _ids(findings: list[dict[str, Any]]) -> list[str]:
    return [f["id"] for f in findings]


class TestContexto:
    def test_recorte_aceito(self) -> None:
        ctx = PLUGIN.validate_context(CTX)
        assert ctx is not None and ctx["model"] == "55" and ctx["jurisdiction"] == "SP"

    @pytest.mark.parametrize(
        "override",
        [{"jurisdiction": "XX"}, {"model": "65"}, {"layout_version": "3.10"}, {"environment": "x"}],
    )
    def test_fora_do_recorte_recusado(self, override: dict[str, str]) -> None:
        with pytest.raises(ValidationError):
            FiscalContext(**{**CTX, **override})

    def test_outra_familia_nao_e_do_plugin(self) -> None:
        assert PLUGIN.validate_context({"family": "cte"}) is None


class TestRun:
    def test_caso_204_completo(self) -> None:
        f = PLUGIN.run({"payload": PROT_204, "domain_context": PLUGIN.validate_context(CTX)})
        assert _ids(f) == ["F-cStat", "F-204", "H1"]
        assert all(x["kind"] == "fact" for x in f[:2]) and f[2]["kind"] == "hypothesis"

    def test_sem_xml_registra_lacuna(self) -> None:
        assert _ids(PLUGIN.run({"domain_context": {}})) == ["G-noxml"]

    def test_xml_hostil_registra_lacuna(self) -> None:
        f = PLUGIN.run(
            {"payload": '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'}
        )
        assert _ids(f) == ["G-xml"]

    def test_controle_positivo_sem_fatos_de_erro(self) -> None:
        assert (
            PLUGIN.run({"payload": POSITIVO, "domain_context": {"environment": "homologation"}})
            == []
        )

    def test_timeout_sem_retorno(self) -> None:
        ctx = PLUGIN.validate_context({**CTX, "events": EVENTS[:2]})
        f = PLUGIN.run({"domain_context": ctx})
        assert _ids(f) == ["G-noxml", "F-timeout", "H-timeout"]

    def test_homologacao_em_contexto_de_producao(self) -> None:
        f = PLUGIN.run({"payload": POSITIVO, "domain_context": {"environment": "production"}})
        assert _ids(f) == ["F-amb"]


def test_run_diagnosis_com_plugin_dfe() -> None:
    seen: dict[str, Any] = {}

    def fake_graph(initial_state):
        seen.update(initial_state)
        findings = PLUGIN.run(initial_state)
        return {**initial_state, "domain_findings": findings,
                "diagnosis": {"probable_root_cause": "Duplicidade por reenvio", "confidence": 0.6}}  # fmt: skip

    with diagnosis_harness("dfe_copilot.plugin", fake_graph):
        r = sdk.run_diagnosis(
            sdk.incident_request(description="NF-e 204", payload=PROT_204, domain_context=CTX)
        )
    assert seen["domain_plugin"] == "dfe"
    assert [x.id for x in r.findings] == ["F-cStat", "F-204", "H1", "H-diagnosis"]
    assert r.cause_status == "hypothesis"


class TestBaseFiscal:
    HIT: ClassVar[dict] = {"evidence_id": "kb:NT_2018.005_v1.51.pdf#p23c0", "rule_ids": ["2B08-20"]}

    def _run(self, lookup) -> list[dict[str, Any]]:
        from dfe_copilot.plugin import DfePlugin

        plugin = DfePlugin(kb_lookup=lookup)
        return plugin.run({"payload": PROT_204, "domain_context": plugin.validate_context(CTX)})

    def test_trecho_vira_fato_de_regra(self) -> None:
        f = self._run(lambda cstat: [self.HIT] if cstat == "204" else [])
        kbf = next(x for x in f if x["id"] == "F-kb")
        assert kbf["source"] == "rule_reference" and kbf["kind"] == "fact"
        assert (
            kbf["evidence_refs"] == ["kb:NT_2018.005_v1.51.pdf#p23c0"]
            and kbf["rule_ref"] == "2B08-20"
        )

    def test_sem_trecho_registra_lacuna(self) -> None:
        assert "G-kb" in _ids(self._run(lambda cstat: []))

    def test_base_indisponivel_registra_lacuna(self) -> None:
        def boom(cstat: str):
            raise ConnectionError("qdrant fora")

        assert "G-kb" in _ids(self._run(boom))

    def test_base_desligada_nao_muda_o_resultado(self) -> None:
        assert _ids(self._run(None)) == ["F-cStat", "F-204", "H1"]

    def test_default_desligado_sem_env(self, monkeypatch) -> None:
        monkeypatch.delenv("DFE_KB_ENABLED", raising=False)
        from dfe_copilot.plugin import _default_kb_lookup

        assert _default_kb_lookup() is None
