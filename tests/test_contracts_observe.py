"""Testes da orquestracao do detector de drift (DA-52).

O nucleo puro (diff/parser) tem testes proprios. Aqui o que importa e' a
ORQUESTRACAO e, sobretudo, os estados que o detector pode assumir sem ter
comparado nada: e' ai que mora o risco de "sem drift" falso.
"""

from __future__ import annotations

import json

import pytest

from app.contracts import observe as observe_module
from app.contracts.diff import (
    SEVERITY_ADDITIVE,
    SEVERITY_BREAKING,
    Change,
    DriftReport,
    ObservationStatus,
    diff_contracts,
    unverified_report,
)
from app.contracts.model import KIND_ODATA, Contract, Entity, Property
from app.contracts.observe import (
    MAX_CHANGES_IN_EVENT,
    check_connector,
    describe_report,
    emit_incident,
    observe,
    to_incident_data,
)
from app.contracts.odata import parse_odata_metadata

EDMX_BEFORE = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">
  <edmx:DataServices>
    <Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="Self">
      <EntityType Name="I_Message">
        <Property Name="MessageId" Type="Edm.String" MaxLength="10">
          <Annotation Term="Org.OData.Core.V1.Key"/>
        </Property>
        <Property Name="StatusText" Type="Edm.String" Nullable="true" MaxLength="80"/>
      </EntityType>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""


def edmx_with(properties: str) -> str:
    return EDMX_BEFORE.replace(
        '<Property Name="StatusText" Type="Edm.String" Nullable="true" MaxLength="80"/>',
        properties,
    )


def _with_key(report, system_key: str):
    """`diff_contracts` e' puro e nao conhece system_key; quem preenche e' o
    observe. Nos testes de evento/texto, reproduzimos esse preenchimento
    para nao ter que gravar em banco so para obter uma chave."""
    return type(report)(
        system_key=system_key,
        status=report.status,
        severity=report.severity,
        changes=report.changes,
        fingerprint_before=report.fingerprint_before,
        fingerprint_after=report.fingerprint_after,
        reason=report.reason,
        kind=report.kind,
    )


@pytest.fixture(autouse=True)
def sem_persistencia(monkeypatch: pytest.MonkeyPatch):
    """Isola a persistencia: o detector funciona sem DATABASE_URL, e um
    teste que grava de verdade transformaria falha de banco em falso
    negativo do diff."""
    monkeypatch.setattr(observe_module.settings, "database_url", "")
    monkeypatch.setattr(observe_module, "_load_baseline", lambda system_key: None)
    persisted: list = []
    monkeypatch.setattr(
        observe_module,
        "_persist",
        lambda report, contract, *, connector_type: persisted.append((report, contract)),
    )
    return persisted


class TestSemBanco:
    def test_primeira_observacao_sem_baseline(self):
        report = observe(
            system_key="cap_prod",
            contract=parse_odata_metadata(EDMX_BEFORE),
            connector_type="odata",
        )
        assert report.status is ObservationStatus.FIRST_OBSERVATION
        assert report.system_key == "cap_prod"

    def test_unverified_quando_nao_ha_contrato(self):
        report = observe(
            system_key="cap_prod",
            contract=None,
            connector_type="odata",
            failure_reason="mock: sem URL",
        )
        assert report.status is ObservationStatus.UNVERIFIED
        assert "mock" in (report.reason or "")

    def test_unverified_nao_e_persistido(self, sem_persistencia):
        # Sem contrato novo, gravar sobrescreveria o baseline anterior sem
        # ganho nenhum -- e o proximo diff compararia contra nada.
        observe(system_key="cap_prod", contract=None, connector_type="odata", failure_reason="x")
        assert sem_persistencia == []

    def test_primeira_observacao_e_persistida(self, sem_persistencia):
        observe(
            system_key="cap_prod",
            contract=parse_odata_metadata(EDMX_BEFORE),
            connector_type="odata",
        )
        assert len(sem_persistencia) == 1
        assert sem_persistencia[0][0].status is ObservationStatus.FIRST_OBSERVATION


class TestDiffAoLongoDoTempo:
    def test_segunda_observacao_identica_e_clean(self, monkeypatch):
        contract = parse_odata_metadata(EDMX_BEFORE)
        monkeypatch.setattr(observe_module, "_load_baseline", lambda key: contract)
        report = observe(system_key="cap_prod", contract=contract, connector_type="odata")
        assert report.status is ObservationStatus.CLEAN

    def test_campo_removido_vira_breaking(self, monkeypatch):
        before = parse_odata_metadata(EDMX_BEFORE)
        after = parse_odata_metadata(
            edmx_with('<Property Name="MessageId" Type="Edm.String" MaxLength="10"/>')
        )
        monkeypatch.setattr(observe_module, "_load_baseline", lambda key: before)
        report = observe(system_key="cap_prod", contract=after, connector_type="odata")
        assert report.status is ObservationStatus.DRIFT
        assert report.is_breaking

    def test_campo_novo_e_additive_sem_incidente(self, monkeypatch):
        before = parse_odata_metadata(EDMX_BEFORE)
        after = parse_odata_metadata(
            edmx_with(
                '<Property Name="StatusText" Type="Edm.String" Nullable="true" MaxLength="80"/>'
                '<Property Name="Severity" Type="Edm.String" Nullable="true"/>'
            )
        )
        monkeypatch.setattr(observe_module, "_load_baseline", lambda key: before)
        report = observe(system_key="cap_prod", contract=after, connector_type="odata")
        assert report.severity == SEVERITY_ADDITIVE
        assert to_incident_data(report, connector_type="odata") is None

    def test_system_key_sobrevive_ao_diff(self, monkeypatch):
        # `diff_contracts` nao conhece o system_key (e' puro); quem preenche
        # e' o observe. Sem isso o report sairia com system_key="" e o
        # evento nao correlacionaria com o catalogo.
        before = parse_odata_metadata(EDMX_BEFORE)
        after = parse_odata_metadata(edmx_with(""))
        monkeypatch.setattr(observe_module, "_load_baseline", lambda key: before)
        report = observe(system_key="cap_prod", contract=after, connector_type="odata")
        assert report.system_key == "cap_prod"


class TestEventoDeIncidente:
    def test_breaking_vira_evento(self):
        before = parse_odata_metadata(EDMX_BEFORE)
        after = parse_odata_metadata(edmx_with(""))
        report = diff_contracts(before, after)
        report = _with_key(report, "cap_prod")
        data = to_incident_data(report, connector_type="odata")
        assert data is not None
        assert "StatusText" in data.description
        assert data.connector_source_system == "cap_prod"

    def test_connector_source_system_usa_system_key_do_catalogo(self):
        # DA-50/invariante 12: com `system_key` a correlacao e' exata. Um
        # rotulo livre tipo "OData" cairia no fallback por connector_type
        # e poderia virar 'ambiguo'.
        before = parse_odata_metadata(EDMX_BEFORE)
        report = diff_contracts(before, parse_odata_metadata(edmx_with("")))
        report = _with_key(report, "sap_odata_prod")
        data = to_incident_data(report, connector_type="odata")
        assert data is not None and data.connector_source_system == "sap_odata_prod"

    def test_logs_carregam_o_report_completo(self):
        before = parse_odata_metadata(EDMX_BEFORE)
        report = diff_contracts(before, parse_odata_metadata(edmx_with("")))
        report = _with_key(report, "s")
        data = to_incident_data(report, connector_type="odata")
        assert data is not None and data.logs is not None
        payload = json.loads(data.logs)
        assert payload["status"] == "drift"
        assert payload["counts"][SEVERITY_BREAKING] >= 1

    def test_clean_nao_vira_evento(self):
        contract = parse_odata_metadata(EDMX_BEFORE)
        report = diff_contracts(contract, contract)
        assert to_incident_data(report, connector_type="odata") is None

    def test_emit_incident_retorna_false_quando_nada_a_emitir(self):
        contract = parse_odata_metadata(EDMX_BEFORE)
        report = diff_contracts(contract, contract)
        assert emit_incident(report, connector_type="odata") is False


class TestDescricao:
    def test_descreve_a_mudanca_breaking(self):
        before = parse_odata_metadata(EDMX_BEFORE)
        report = diff_contracts(before, parse_odata_metadata(edmx_with("")))
        report = _with_key(report, "s")
        text = describe_report(report)
        assert "BREAKING" in text
        assert "removed" in text or "removido" in text

    def test_unverified_diz_que_nao_verificou(self):
        assert "nao pode ser verificado" in describe_report(unverified_report("s", "HTTP 500"))

    def test_many_changes_truncam_com_aviso(self):
        changes = tuple(
            Change(
                severity=SEVERITY_BREAKING,
                kind="property_removed",
                entity="E",
                property=f"P{i}",
                old="Edm.String",
            )
            for i in range(MAX_CHANGES_IN_EVENT + 5)
        )
        report = DriftReport(
            system_key="s",
            status=ObservationStatus.DRIFT,
            severity=SEVERITY_BREAKING,
            changes=changes,
        )
        text = describe_report(report)
        assert "e mais 5" in text

    def test_descricao_respeita_max_length_do_modelo(self):
        # A descricao vira `IncidentRequest.description`, que tem teto.
        # Um contrato com centenas de campos nao pode estourar o limite
        # e ser rejeitado pelo Pydantic no meio da emissao do evento.
        from app.contracts.diff import Change, DriftReport
        from app.models import MAX_DESCRIPTION_LENGTH

        changes = tuple(
            Change(
                severity=SEVERITY_BREAKING,
                kind="property_removed",
                entity="E",
                property=f"CampoComNomeBemLongo{i}",
                old="Edm.String",
                hint="dica " * 20,
            )
            for i in range(200)
        )
        report = DriftReport(
            system_key="s",
            status=ObservationStatus.DRIFT,
            severity=SEVERITY_BREAKING,
            changes=changes,
        )
        text = describe_report(report)
        assert len(text) < MAX_DESCRIPTION_LENGTH


class TestCheckConnector:
    def test_conector_sem_contrato_da_unverified(self):
        class SemContrato:
            def fetch_contract(self):
                return None

        report = check_connector(SemContrato(), system_key="s", connector_type="odata")
        assert report.status is ObservationStatus.UNVERIFIED

    def test_conector_real_gera_primeira_observacao(self):
        class ComContrato:
            def fetch_contract(self):
                return parse_odata_metadata(EDMX_BEFORE)

        report = check_connector(ComContrato(), system_key="s", connector_type="odata")
        assert report.status is ObservationStatus.FIRST_OBSERVATION

    def test_erro_de_uso_vira_unverified_com_motivo(self):
        class Quebrado:
            def fetch_contract(self):
                raise ValueError("cliente injetado invalido")

        report = check_connector(Quebrado(), system_key="s", connector_type="odata")
        assert report.status is ObservationStatus.UNVERIFIED
        assert "cliente injetado invalido" in (report.reason or "")

    def test_base_tem_fetch_contract_padrao_para_os_outros_7_conectores(self):
        # Interface segregada: quem nao tem introspeccao herda None em vez
        # de ser obrigar a devolver um contrato vazio.
        from app.connectors.base import SAPConnector

        class Qualquer(SAPConnector):
            def fetch(self, identifier):
                from app.connectors.base import ConnectorResult

                return ConnectorResult("x", "ok", None, "", "")

        assert Qualquer().fetch_contract() is None


class TestContratoVazio:
    def test_contrato_sem_entidades_nao_e_saudavel(self):
        empty = Contract(kind=KIND_ODATA, entities={})
        report = observe(system_key="s", contract=empty, connector_type="odata")
        # Sem baseline, e' primeira observacao -- e NAO clean.
        assert report.status is not ObservationStatus.CLEAN

    def test_propriedades_do_contrato(self):
        contract = parse_odata_metadata(EDMX_BEFORE)
        assert contract.property_count() == 2
        assert contract.entity_names() == ["I_Message"]
        assert isinstance(contract.entities["I_Message"], Entity)
        assert contract.entities["I_Message"].properties["MessageId"].key is True
        assert isinstance(contract.entities["I_Message"].properties["StatusText"], Property)
