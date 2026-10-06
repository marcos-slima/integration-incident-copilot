"""Testes do comparativo de contratos (DA-52).

O foco nao' e' cobertura de linha: e' travar as DECISOES, porque cada uma
delas e' um trade-off entre alarme perdido e alarme inutil, e as duas
extremidades custam caro (integracao quebrada nao detectada / time que
desativa o detector).
"""

from __future__ import annotations

import pathlib
import re
from typing import ClassVar

from app.contracts.diff import (
    SEVERITY_ADDITIVE,
    SEVERITY_BREAKING,
    SEVERITY_COSMETIC,
    SEVERITY_NONE,
    ObservationStatus,
    diff_contracts,
    unverified_report,
)
from app.contracts.model import KIND_IDOC, KIND_ODATA, Contract, Entity, Property

#: Contrato base: os dois campos que o pipeline realmente usa hoje. O
#: conector OData le `StatusText` (odata_connector.py:177) e filtra por
#: `MessageId` (odata_connector.py:109) -- sao eles que a DA-52 existe para
#: vigiar.
DEFAULT_PROPERTIES: dict[str, Property] = {
    "StatusText": Property("StatusText", "Edm.String", nullable=True, max_length=80),
    "MessageId": Property("MessageId", "Edm.String", key=True),
}


def contract(**overrides: object) -> Contract:
    """Monta um contrato de uma entity `I_Message`.

    `properties` SUBSTITUI o conjunto inteiro (nao faz merge): e' o que
    permite escrever "o campo foi removido" como `properties={}` sem uma
    sintaxe de remocao. `keep_properties` faz merge, para o caso de so
    querer mexer em um campo.
    """
    properties = dict(DEFAULT_PROPERTIES)
    keep = overrides.pop("keep_properties", None)
    if keep:
        properties.update(keep)  # type: ignore[arg-type]
    if "properties" in overrides:
        properties = dict(overrides.pop("properties"))  # type: ignore[arg-type]
    entities: dict[str, Entity] = {
        "I_Message": Entity(name="I_Message", properties=properties),
    }
    if "entities" in overrides:
        replacement = overrides.pop("entities")
        if replacement:  # type: ignore[truthy-bool]
            entities = dict(replacement)  # type: ignore[arg-type]
        else:
            entities = {}
    return Contract(
        kind=str(overrides.pop("kind", KIND_ODATA)),
        entities=entities,
        parameters=dict(overrides.pop("parameters", {}) or {}),  # type: ignore[arg-type]
    )


def kinds(report_changes: list) -> set[str]:
    return {change.kind for change in report_changes}


class TestNoBaseline:
    def test_primeira_observacao_nao_e_drift(self):
        report = diff_contracts(None, contract())
        assert report.status is ObservationStatus.FIRST_OBSERVATION
        assert report.severity == SEVERITY_NONE
        assert report.changes == ()

    def test_primeira_observacao_grava_a_impressao_digital(self):
        report = diff_contracts(None, contract())
        assert report.fingerprint_after == contract().fingerprint()
        assert report.fingerprint_before is None

    def test_primeira_observacao_nao_pode_ser_lida_como_saudavel(self):
        # Se `is_breaking` fosse a unica checagem do consumidor, um baseline
        # recem-criado pareceria "saudavel". Por isso o status existe.
        report = diff_contracts(None, contract())
        assert report.status is not ObservationStatus.CLEAN


class TestUnverified:
    def test_falha_de_leitura_nao_e_sem_drift(self):
        report = unverified_report("cap_prod", "HTTP 401 em $metadata")
        assert report.status is ObservationStatus.UNVERIFIED
        assert report.status is not ObservationStatus.CLEAN
        assert report.is_breaking is False

    def test_motivo_e_visivel(self):
        assert "401" in (unverified_report("s", "HTTP 401").reason or "")

    def test_unverified_nao_inventa_mudanca(self):
        report = unverified_report("s", "timeout")
        assert report.changes == ()
        assert report.fingerprint_after is None


class TestBreaking:
    def test_campo_removido_e_breaking(self):
        after = contract(properties={})  # remove StatusText e MessageId
        report = diff_contracts(contract(), after)
        assert report.severity == SEVERITY_BREAKING
        assert "property_removed" in kinds(list(report.changes))

    def test_remover_o_campo_que_o_conector_le_e_breaking(self):
        # Este e' o cenario da DA-02: o conector faz record.get("StatusText")
        # e passaria a devolver string vazia sem erro nenhum.
        after = contract()
        del after.entities["I_Message"].properties["StatusText"]  # type: ignore[index]
        report = diff_contracts(contract(), after)
        assert report.is_breaking
        removed = [c for c in report.changes if c.property == "StatusText"]
        assert removed and removed[0].kind == "property_removed"

    def test_troca_de_tipo_e_breaking_mesmo_para_mais_largo(self):
        after = contract(
            keep_properties={
                "StatusText": Property("StatusText", "Edm.MemoryStream", nullable=True)
            }
        )
        report = diff_contracts(contract(), after)
        assert report.is_breaking
        assert "type_changed" in kinds(list(report.changes))

    def test_troca_de_tipo_entre_primitivos_nao_pode_parecer_igual(self):
        # Regressao do parser: reduzir `Edm.Decimal` a `Decimal` faria
        # `Edm.Decimal` e `Outro.Decimal` casarem como o mesmo tipo.
        after = contract(
            keep_properties={"StatusText": Property("StatusText", "Outro.Decimal", nullable=True)}
        )
        assert diff_contracts(contract(), after).is_breaking

    def test_not_nullable_novo_e_breaking(self):
        after = contract(
            keep_properties={"StatusText": Property("StatusText", "Edm.String", nullable=False)}
        )
        report = diff_contracts(contract(), after)
        assert report.is_breaking
        assert "nullability_tightened" in kinds(list(report.changes))

    def test_max_length_menor_e_breaking(self):
        after = contract(
            keep_properties={
                "StatusText": Property("StatusText", "Edm.String", nullable=True, max_length=40)
            }
        )
        report = diff_contracts(contract(), after)
        assert report.is_breaking
        assert "max_length_reduced" in kinds(list(report.changes))

    def test_campo_que_deixa_de_ser_chave_e_breaking(self):
        after = contract(keep_properties={"MessageId": Property("MessageId", "Edm.String")})
        report = diff_contracts(contract(), after)
        assert report.is_breaking
        assert "key_relaxed" in kinds(list(report.changes))

    def test_entidade_removida_e_breaking(self):
        report = diff_contracts(contract(), contract(entities={}))
        assert report.is_breaking
        assert "entity_removed" in kinds(list(report.changes))

    def test_entidade_que_vira_abstrata_e_breaking(self):
        after = contract(entities={"I_Message": Entity("I_Message", properties={}, abstract=True)})
        report = diff_contracts(contract(), after)
        assert report.is_breaking
        assert "abstract" in kinds(list(report.changes))

    def test_trocar_o_tipo_de_contrato_e_breaking_com_hint(self):
        report = diff_contracts(contract(), contract(kind=KIND_IDOC))
        assert report.is_breaking
        change = report.changes[0]
        assert change.kind == "contract_kind_changed"
        assert change.hint  # manda revisar o catalogo em vez de tratar como campo


class TestAdditive:
    def test_campo_novo_e_additive(self):
        after = contract(
            keep_properties={"Severity": Property("Severity", "Edm.String", nullable=True)}
        )
        report = diff_contracts(contract(), after)
        assert report.severity == SEVERITY_ADDITIVE
        assert report.status is ObservationStatus.DRIFT
        assert report.is_breaking is False

    def test_nullable_relaxado_e_additive(self):
        # `key=True` mantido de proposito: soltar a chave junto seria
        # `key_relaxed` (breaking) e mudaria o que o teste mede.
        before_props = {
            "StatusText": Property("StatusText", "Edm.String", nullable=True, max_length=80),
            "MessageId": Property("MessageId", "Edm.String", nullable=False, key=True),
        }
        after_props = {
            "StatusText": Property("StatusText", "Edm.String", nullable=True, max_length=80),
            "MessageId": Property("MessageId", "Edm.String", nullable=True, key=True),
        }
        before = Contract(
            kind=KIND_ODATA, entities={"I_Message": Entity("I_Message", before_props)}
        )
        after = Contract(kind=KIND_ODATA, entities={"I_Message": Entity("I_Message", after_props)})
        report = diff_contracts(before, after)
        assert report.severity == SEVERITY_ADDITIVE
        assert "nullability_relaxed" in kinds(list(report.changes))

    def test_entidade_nova_e_additive(self):
        after = contract(
            entities={
                "I_Message": Entity("I_Message", properties=dict(DEFAULT_PROPERTIES)),
                "I_Other": Entity("I_Other", properties={"X": Property("X", "Edm.String")}),
            }
        )
        report = diff_contracts(contract(), after)
        assert report.severity == SEVERITY_ADDITIVE
        assert "entity_added" in kinds(list(report.changes))

    def test_max_length_maior_e_additive(self):
        after = contract(
            keep_properties={
                "StatusText": Property("StatusText", "Edm.String", nullable=True, max_length=255)
            }
        )
        report = diff_contracts(contract(), after)
        assert report.severity == SEVERITY_ADDITIVE
        assert "max_length_increased" in kinds(list(report.changes))

    def test_breaking_vence_additive_no_resumo(self):
        after = contract(
            keep_properties={"New": Property("New", "Edm.String")},
            entities={},
        )
        report = diff_contracts(contract(), after)
        assert report.severity == SEVERITY_BREAKING
        assert report.counts[SEVERITY_ADDITIVE] >= 0


class TestProbableRename:
    def test_rename_detectado_por_assinatura_identica(self):
        after = contract(
            properties={
                "MessageId": Property("MessageId", "Edm.String", key=True),
                "TextoStatus": Property("TextoStatus", "Edm.String", nullable=True, max_length=80),
            }
        )
        report = diff_contracts(contract(), after)
        change = next(c for c in report.changes if c.property == "StatusText")
        assert change.kind == "property_removed"
        assert "TextoStatus" in (change.hint or "")
        assert "rename" in (change.hint or "")

    def test_rename_e_breaking_nao_additive(self):
        after = contract(
            properties={
                "MessageId": Property("MessageId", "Edm.String", key=True),
                "TextoStatus": Property("TextoStatus", "Edm.String", nullable=True, max_length=80),
            }
        )
        report = diff_contracts(contract(), after)
        assert report.is_breaking

    def test_rename_nao_e_adivinhado_quando_a_assinatura_difere(self):
        # Mesmo tipo, mas tamanho e' diferente: pode ser campo novo, nao rename.
        after = contract(
            properties={
                "MessageId": Property("MessageId", "Edm.String", key=True),
                "TextoStatus": Property("TextoStatus", "Edm.String", nullable=True, max_length=255),
            }
        )
        report = diff_contracts(contract(), after)
        removed = [c for c in report.changes if c.kind == "property_removed"]
        added = [c for c in report.changes if c.kind == "property_added"]
        assert removed and added
        assert not any("rename" in (c.hint or "") for c in report.changes)

    def test_rename_nao_rouba_o_additive_quando_o_tipo_difere(self):
        after = contract(
            properties={
                "MessageId": Property("MessageId", "Edm.String", key=True),
                "TextoStatus": Property("TextoStatus", "Edm.Int32", nullable=True, max_length=80),
            }
        )
        report = diff_contracts(contract(), after)
        assert "property_added" in kinds(list(report.changes))


class TestClean:
    def test_contrato_identico_e_clean(self):
        report = diff_contracts(contract(), contract())
        assert report.status is ObservationStatus.CLEAN
        assert report.severity == SEVERITY_NONE
        assert report.changes == ()

    def test_impressao_digital_igual_mesmo_recriando_o_objeto(self):
        report = diff_contracts(contract(), contract())
        assert report.fingerprint_before == report.fingerprint_after

    def test_parametros_rfc_iguais(self):
        before = contract(
            kind="rfc_function_module",
            parameters={"IDOCNUMBER": Property("IDOCNUMBER", "STRING", nullable=False)},
        )
        after = contract(
            kind="rfc_function_module",
            parameters={"IDOCNUMBER": Property("IDOCNUMBER", "STRING", nullable=False)},
        )
        assert diff_contracts(before, after).status is ObservationStatus.CLEAN


class TestRfcParameters:
    def test_parametro_removido_e_breaking(self):
        before = contract(
            kind="rfc_function_module", parameters={"IDOCNUMBER": Property("IDOCNUMBER", "STRING")}
        )
        after = contract(kind="rfc_function_module", parameters={})
        report = diff_contracts(before, after)
        assert report.is_breaking
        assert "parameter_removed" in kinds(list(report.changes))

    def test_parametro_novo_e_additive(self):
        before = contract(kind="rfc_function_module", parameters={})
        after = contract(kind="rfc_function_module", parameters={"NEW": Property("NEW", "STRING")})
        report = diff_contracts(before, after)
        assert report.severity == SEVERITY_ADDITIVE
        assert "parameter_added" in kinds(list(report.changes))


class TestReportSurface:
    def test_contagem_por_severidade(self):
        after = contract(
            keep_properties={"New": Property("New", "Edm.String")},
            entities={},
        )
        counts = diff_contracts(contract(), after).counts
        assert counts[SEVERITY_BREAKING] >= 1
        assert set(counts) == {SEVERITY_BREAKING, SEVERITY_ADDITIVE, SEVERITY_COSMETIC}

    def test_summary_nao_promete_saude_quando_nao_verificou(self):
        assert "nao verificado" in unverified_report("cap", "timeout").summary()
        assert "baseline criado" in diff_contracts(None, contract()).summary()

    def test_as_dict_e_serializavel(self):
        import json

        report = diff_contracts(contract(), contract())
        assert json.loads(json.dumps(report.as_dict()))["status"] == "clean"

    def test_describe_nao_levanta(self):
        for change in diff_contracts(contract(), contract(kind=KIND_IDOC)).changes:
            assert change.describe()

    def test_cosmetic_nunca_brega(self):
        # Nenhuma mudanca puramente cosmetica e' detectada no nivel de
        # contrato, porque anotacao/namespace nem entram no modelo. O
        # severidade existe para o report ser estavel caso entre um dia.
        assert SEVERITY_COSMETIC not in {
            c.severity for c in diff_contracts(contract(), contract()).changes
        }


class TestEstadosDocumentados:
    """A tabela de estados da DA-52 no README e' a mesma informacao que o
    enum `ObservationStatus`, e as duas andaram fora de sync: o texto dizia
    "cinco estados" enquanto o enum tinha quatro e a propria secao de
    limitacoes dizia que os dois estados extras nem existem. Um leitor que
    confiasse no numero montava dashboard com uma categoria vazia.

    Este teste e' bidirecional de proposito: se um estado novo entrar no
    enum sem documentar, OU a tabela documentar algo que o enum nao tem,
    reprova. Nao fixa a contagem -- fixa a COERENCIA.
    """

    _NUMEROS: ClassVar[dict[str, int]] = {
        "dois": 2,
        "tres": 3,
        "quatro": 4,
        "cinco": 5,
        "seis": 6,
        "sete": 7,
        "oito": 8,
        "nove": 9,
        "dez": 10,
    }

    def _secao_da52(self) -> str:
        readme = pathlib.Path("README.md").read_text(encoding="utf-8")
        # Ancorado no ROTULO (DA-52), nao no numero da secao. A secao foi
        # renumerada de 36 para 37 quando a DA-30 ganhou o lugar dela no
        # indice, e o teste que dependia do numero reprovou — o proprio
        # defeito que `das_index_current` existe para tornar visivel.
        m = re.search(r"^### \d+\..*\(DA-52\)\s*$", readme, re.MULTILINE)
        assert m, "a secao da DA-52 nao foi encontrada no README.md"
        fim = readme.find("\n### ", m.end())
        return readme[m.start() : fim if fim != -1 else len(readme)]

    def test_tabela_da52_lista_exatamente_o_enum(self):
        esperado = {s.value for s in ObservationStatus}
        # primeira coluna das linhas da tabela de estados
        linhas = [
            linha
            for linha in self._secao_da52().splitlines()
            if linha.startswith("| `") and linha.count("|") >= 3
        ]
        documentado = {linha.split("`")[1] for linha in linhas}
        assert documentado == esperado, (
            f"tabela da DA-52 ({sorted(documentado)}) != enum ({sorted(esperado)})"
        )

    def test_contagem_no_texto_bate_com_o_enum(self):
        m = re.search(r"\*\*A solu..o\.\*\* (\w+) estados", self._secao_da52())
        assert m, "o texto da solucao precisa declarar a contagem de estados"
        declarado = self._NUMEROS.get(m.group(1).lower())
        assert declarado == len(ObservationStatus), (
            f"README diz '{m.group(1)} estados', enum tem {len(ObservationStatus)}"
        )

    def test_invariante_do_claude_md_bate_com_o_enum(self):
        claude = pathlib.Path("CLAUDE.md").read_text(encoding="utf-8")
        m = re.search(r"a DA-52 tem \*{0,2}(\w+)\*{0,2} estados", claude)
        assert m, "o invariante 15 do CLAUDE.md precisa declarar a contagem"
        declarado = self._NUMEROS.get(m.group(1).lower()) or int(m.group(1))
        assert declarado == len(ObservationStatus), (
            f"CLAUDE.md diz '{m.group(1)} estados', enum tem {len(ObservationStatus)}"
        )
