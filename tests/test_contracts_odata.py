"""Testes da leitura de `$metadata` (DA-52)."""

from __future__ import annotations

import pytest

from app.contracts.model import KIND_ODATA
from app.contracts.odata import (
    MetadataError,
    contract_from_dict,
    parse_odata_metadata,
)

EDMX_V4 = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">
  <edmx:DataServices>
    <Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="Self">
      <EntityType Name="I_Currency">
        <Property Name="Currency" Type="Edm.String" MaxLength="3">
          <Annotation Term="Org.OData.Core.V1.Key"/>
        </Property>
        <Property Name="StatusText" Type="Edm.String" Nullable="true" MaxLength="80"/>
        <Property Name="Amount" Type="Edm.Decimal" Nullable="true"/>
        <NavigationProperty Name="Text" Type="Self.I_CurrencyText"/>
      </EntityType>
      <EntityType Name="I_CurrencyText" Abstract="true">
        <Property Name="Language" Type="Edm.String" MaxLength="2"/>
      </EntityType>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""

EDMX_LEGACY_KEY = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx">
  <Schema Namespace="Self">
    <EntityType Name="I_Doc">
      <Key>
        <PropertyRef Name="DocId"/>
      </Key>
      <Property Name="DocId" Type="Edm.String" MaxLength="10"/>
      <Property Name="Total" Type="Edm.Decimal"/>
    </EntityType>
  </Schema>
</edmx:Edmx>
"""


class TestParseODataMetadata:
    def test_extrai_entidades_e_campos(self):
        contract = parse_odata_metadata(EDMX_V4)
        assert contract.kind == KIND_ODATA
        assert contract.entity_names() == ["I_Currency", "I_CurrencyText"]
        currency = contract.entities["I_Currency"]
        assert set(currency.properties) == {
            "Currency",
            "StatusText",
            "Amount",
            "Text",
        }

    def test_ausencia_de_nullable_means_not_nullable(self):
        # Padrao EDMX. Ler como nullable (default de XML) faria todo campo
        # parecer relaxado e mascararia nullability_tightened.
        currency = parse_odata_metadata(EDMX_V4).entities["I_Currency"]
        assert currency.properties["Currency"].nullable is False

    def test_nullable_explicito_e_respeitado(self):
        currency = parse_odata_metadata(EDMX_V4).entities["I_Currency"]
        assert currency.properties["StatusText"].nullable is True

    def test_chave_via_annotation(self):
        currency = parse_odata_metadata(EDMX_V4).entities["I_Currency"]
        assert currency.properties["Currency"].key is True
        assert currency.properties["StatusText"].key is False

    def test_chave_via_elemento_key_legado(self):
        contract = parse_odata_metadata(EDMX_LEGACY_KEY)
        assert contract.entities["I_Doc"].properties["DocId"].key is True

    def test_abstract(self):
        contract = parse_odata_metadata(EDMX_V4)
        assert contract.entities["I_CurrencyText"].abstract is True
        assert contract.entities["I_Currency"].abstract is False

    def test_navigation_property_marcada(self):
        currency = parse_odata_metadata(EDMX_V4).entities["I_Currency"]
        assert currency.properties["Text"].navigation is True
        assert currency.properties["Currency"].navigation is False

    def test_namespace_completo_do_tipo_e_reduzido(self):
        currency = parse_odata_metadata(EDMX_V4).entities["I_Currency"]
        assert currency.properties["Text"].type_name == "I_CurrencyText"
        assert currency.properties["Amount"].type_name == "Edm.Decimal"

    def test_max_length_inteiro(self):
        currency = parse_odata_metadata(EDMX_V4).entities["I_Currency"]
        assert currency.properties["Currency"].max_length == 3

    def test_max_length_nao_numerico_vira_ausente(self):
        xml = EDMX_V4.replace('MaxLength="3"', 'MaxLength="max"')
        currency = parse_odata_metadata(xml).entities["I_Currency"]
        assert currency.properties["Currency"].max_length is None

    def test_xml_invalido_levanta(self):
        with pytest.raises(MetadataError):
            parse_odata_metadata("<Schema>")

    def test_raiz_inesperada_levanta(self):
        with pytest.raises(MetadataError):
            parse_odata_metadata("<html><body/></html>")

    def test_metadata_sem_entity_type_levanta(self):
        # Contrato vazio nao pode virar "sem drift": seria o pior falso
        # verde possivel neste detector.
        with pytest.raises(MetadataError):
            parse_odata_metadata(
                '<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">'
                "<edmx:DataServices><Schema/></edmx:DataServices></edmx:Edmx>"
            )


class TestVolatileFieldsAreIgnored:
    """A armadilha real do SAP Gateway: o `$metadata` muda a cada publish
    sem que nenhum campo tenha mudado. Se qualquer um destes entrar no
    contrato, o detector vira alarme constante."""

    def test_annotacao_de_version_nao_muda_a_impressao_digital(self):
        base = parse_odata_metadata(EDMX_V4)
        bumped = EDMX_V4.replace(
            "</edmx:DataServices>",
            '<Annotation Term="OData.Community.V1.VocabularyMetadata">'
            '<Annotation Term="OData.Community.V1.VocabularyMetadata#Version" '
            'String="4.0.1"/></Annotation></edmx:DataServices>',
        )
        assert parse_odata_metadata(bumped).fingerprint() == base.fingerprint()

    def test_namespace_novo_nao_muda_a_impressao_digital(self):
        base = parse_odata_metadata(EDMX_V4)
        renamed = EDMX_V4.replace('Namespace="Self"', 'Namespace="Self.a1b2c3d4"')
        renamed = renamed.replace('Type="Self.', 'Type="Self.a1b2c3d4.')
        assert parse_odata_metadata(renamed).fingerprint() == base.fingerprint()

    def test_espaco_e_quebra_de_linha_nao_mudam_a_impressao_digital(self):
        base = parse_odata_metadata(EDMX_V4)
        reformatted = EDMX_V4.replace("\n", "\n\n   ").replace("  ", " ")
        assert parse_odata_metadata(reformatted).fingerprint() == base.fingerprint()

    def test_ordem_de_declaracao_nao_muda_a_impressao_digital(self):
        # Mesmo contrato, campos declarados em outra ordem. O SAP reordena
        # Property ao republicar o servico; se a ordem entrasse no hash, toda
        # publicacao viria como drift.
        reordered = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">
  <edmx:DataServices>
    <Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="Self">
      <EntityType Name="I_Currency">
        <NavigationProperty Name="Text" Type="Self.I_CurrencyText"/>
        <Property Name="Amount" Type="Edm.Decimal" Nullable="true"/>
        <Property Name="StatusText" Type="Edm.String" Nullable="true" MaxLength="80"/>
        <Property Name="Currency" Type="Edm.String" MaxLength="3">
          <Annotation Term="Org.OData.Core.V1.Key"/>
        </Property>
      </EntityType>
      <EntityType Name="I_CurrencyText" Abstract="true">
        <Property Name="Language" Type="Edm.String" MaxLength="2"/>
      </EntityType>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""
        assert parse_odata_metadata(reordered).entity_names() == (
            parse_odata_metadata(EDMX_V4).entity_names()
        )
        assert parse_odata_metadata(reordered).fingerprint() == (
            parse_odata_metadata(EDMX_V4).fingerprint()
        )
        assert reordered != EDMX_V4, "o XML de teste nao foi realmente reordenado"
        assert parse_odata_metadata(reordered).fingerprint() == (
            parse_odata_metadata(EDMX_V4).fingerprint()
        )

    def test_impressao_digital_e_deterministica_entre_chamadas(self):
        first = parse_odata_metadata(EDMX_V4).fingerprint()
        for _ in range(5):
            assert parse_odata_metadata(EDMX_V4).fingerprint() == first


class TestRoundTrip:
    def test_ida_e_volta_preserva_a_impressao_digital(self):
        # O baseline e' persistido como JSON; se o round trip mudasse a
        # impressao digital, toda observacao acusaria drift.
        original = parse_odata_metadata(EDMX_V4)
        restored = contract_from_dict(original.as_dict())
        assert restored.fingerprint() == original.fingerprint()
        assert restored.kind == original.kind

    def test_round_trip_do_legado_tambem(self):
        original = parse_odata_metadata(EDMX_LEGACY_KEY)
        assert contract_from_dict(original.as_dict()).fingerprint() == original.fingerprint()

    def test_dict_vazio_nao_quebra(self):
        contract = contract_from_dict({})
        assert contract.entities == {}
        assert contract.fingerprint()
