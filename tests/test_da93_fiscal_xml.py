"""DA-93 fase 2: ferramentas fiscais deterministicas (app/fiscal/xml_tools.py).

Fixtures sinteticos: CNPJ ficticio 11.222.333/0001-81, recorte SP (cUF 35),
homologacao. Nenhum documento real, nenhuma chamada de rede.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.fiscal import (
    FiscalXmlError,
    SchemaPackage,
    analyze_fiscal_xml,
    parse_access_key,
    safe_parse,
)
from app.fiscal.xml_tools import NS, SCHEMAS_DIR, validate_xsd

KEY_SP = "35261111222333000181550010000000011000000014"  # DV 4 (mod 11)

PROT_204 = f"""<?xml version="1.0" encoding="UTF-8"?>
<protNFe xmlns="{NS}" versao="4.00"><infProt>
  <tpAmb>2</tpAmb><verAplic>DEMO_IIC</verAplic><chNFe>{KEY_SP}</chNFe>
  <dhRecbto>2026-11-11T10:00:03-03:00</dhRecbto>
  <cStat>204</cStat><xMotivo>Rejeicao: Duplicidade de NF-e</xMotivo>
</infProt></protNFe>""".encode()

NFE_INCOMPLETA = (
    f'<NFe xmlns="{NS}"><infNFe versao="4.00" Id="NFe{KEY_SP}"><ide><cUF>35</cUF></ide></infNFe></NFe>'
).encode()


@pytest.fixture(scope="module")
def package() -> SchemaPackage:
    return SchemaPackage.load()


class TestChaveDeAcesso:
    def test_chave_sintetica_sp_valida(self) -> None:
        k = parse_access_key(KEY_SP)
        assert k is not None and k.dv_valid
        assert (k.cuf, k.model, k.serie, k.number, k.dv) == ("35", "55", "001", "000000001", "4")

    def test_dv_errado_invalida(self) -> None:
        assert parse_access_key(KEY_SP[:43] + "0").dv_valid is False  # type: ignore[union-attr]

    def test_cuf_inexistente_invalida(self) -> None:
        body = "99" + KEY_SP[2:43]
        from app.fiscal.xml_tools import _mod11_dv

        assert parse_access_key(body + _mod11_dv(body)).dv_valid is False  # type: ignore[union-attr]

    @pytest.mark.parametrize("raw", [None, "", "123", KEY_SP + "1", "A" * 44])
    def test_formato_invalido_nao_e_chave(self, raw: str | None) -> None:
        assert parse_access_key(raw) is None


class TestParseSeguro:
    @pytest.mark.parametrize(
        ("xml", "code"),
        [
            (b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>', "dtd_forbidden"),
            (
                b'<!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><x>&b;</x>',
                "dtd_forbidden",
            ),
            (b"<x>", "malformed_xml"),
            (b"<x>" + b"a" * 1_000_001 + b"</x>", "input_too_large"),
        ],
    )
    def test_entrada_hostil_recusada(self, xml: bytes, code: str) -> None:
        with pytest.raises(FiscalXmlError) as exc:
            safe_parse(xml)
        assert exc.value.code == code


class TestProtocolo204:
    def test_fatos_do_retorno(self, package: SchemaPackage) -> None:
        r = analyze_fiscal_xml(PROT_204, package)
        assert r["document_cStat"] == "204" and r["tpAmb"] == "2"
        assert r["access_key_dv_valid"] is True
        assert [f["id"] for f in r["findings"]] == ["F-cStat"]
        assert all(f["kind"] == "fact" for f in r["findings"])
        assert r["signature_validation"] == "not_executed"

    def test_pacote_sem_schema_de_protocolo_nao_executa(self, package: SchemaPackage) -> None:
        r = analyze_fiscal_xml(PROT_204, package)
        assert r["xsd_validation"] == "not_executed"
        assert r["xsd"]["reason"] == "no_entrypoint:protNFe@4.00"

    def test_status_de_lote_nao_e_lido(self, package: SchemaPackage) -> None:
        lote = f'<retEnviNFe xmlns="{NS}" versao="4.00"><cStat>103</cStat></retEnviNFe>'.encode()
        assert analyze_fiscal_xml(lote, package).get("document_cStat") is None

    def test_namespace_alheio_nao_e_fiscal(self, package: SchemaPackage) -> None:
        r = analyze_fiscal_xml(PROT_204.replace(NS.encode(), b"urn:nao-fiscal"), package)
        assert r["fiscal_namespace"] is False and r["findings"] == []

    def test_dv_invalido_vira_fato(self, package: SchemaPackage) -> None:
        r = analyze_fiscal_xml(
            PROT_204.replace(KEY_SP.encode(), (KEY_SP[:43] + "0").encode()), package
        )
        assert "F-dv" in [f["id"] for f in r["findings"]]

    def test_hash_sobre_bytes_originais(self, package: SchemaPackage) -> None:
        import hashlib

        assert (
            analyze_fiscal_xml(PROT_204, package)["raw_sha256"]
            == hashlib.sha256(PROT_204).hexdigest()
        )


class TestXsd:
    def test_pacote_oficial_carrega_com_hash(self, package: SchemaPackage) -> None:
        assert package.manifest["zip_sha256"].startswith("b329ecbe")
        assert package.entrypoint("NFe", "4.00") is not None

    def test_nfe_incompleta_invalida_com_erros(self, package: SchemaPackage) -> None:
        r = analyze_fiscal_xml(NFE_INCOMPLETA, package)
        assert r["xsd_validation"] == "invalid"
        assert r["xsd"]["errors"] and "Missing child element" in r["xsd"]["errors"][0]["message"]
        assert "F-xsd" in [f["id"] for f in r["findings"]]

    def test_sem_pacote_nao_executa(self) -> None:
        r = analyze_fiscal_xml(NFE_INCOMPLETA, None)
        assert r["xsd_validation"] == "not_executed"

    def test_versao_sem_entrypoint_nao_executa(self, package: SchemaPackage) -> None:
        xml = NFE_INCOMPLETA.replace(b'versao="4.00"', b'versao="3.10"')
        assert validate_xsd(safe_parse(xml), package)["status"] == "not_executed"

    def test_xsd_adulterado_e_recusado(self, tmp_path: Path) -> None:
        base = tmp_path / "nfe"
        shutil.copytree(SCHEMAS_DIR, base)
        alvo = base / "PL_010g_v1.03" / "NFe" / "nfe_v4.00.xsd"
        alvo.write_bytes(alvo.read_bytes() + b"<!-- adulterado -->")
        with pytest.raises(FiscalXmlError) as exc:
            SchemaPackage.load(base=base)
        assert exc.value.code == "schema_hash_mismatch"

    def test_schema_remoto_e_recusado(self, tmp_path: Path) -> None:
        base = tmp_path / "nfe"
        pkg = base / "PKG"
        pkg.mkdir(parents=True)
        (pkg / "remoto.xsd").write_text(
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            f'targetNamespace="{NS}" xmlns="{NS}">'
            '<xs:include schemaLocation="https://exemplo.invalid/x.xsd"/>'
            '<xs:element name="NFe" type="xs:string"/></xs:schema>'
        )
        manifest = {"files_sha256": {}, "entrypoints": {"NFe@4.00": "remoto.xsd"}}
        (base / "PKG.manifest.json").write_text(json.dumps(manifest))
        remoto = SchemaPackage.load("PKG", base=base)
        with pytest.raises(Exception):  # noqa: B017 - lxml embrulha o erro do resolver
            validate_xsd(safe_parse(NFE_INCOMPLETA), remoto)
