"""DA-93 fase 2: ferramentas fiscais deterministicas (app/fiscal/xml_tools.py).

Fixtures sinteticos: CNPJ ficticio 11.222.333/0001-81, recorte SP (cUF 35),
homologacao. Nenhum documento real, nenhuma chamada de rede.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from dfe_copilot.fiscal import (
    FiscalXmlError,
    SchemaPackage,
    analyze_fiscal_xml,
    parse_access_key,
    safe_parse,
)
from dfe_copilot.fiscal.xml_tools import NS, SCHEMAS_DIR, validate_xsd
from lxml import etree
from signing import make_cert, sign

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
        from dfe_copilot.fiscal.xml_tools import _mod11_dv

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
        ],
        # ids explicitos: com bytes como parametro o pytest usa o conteudo como
        # id do teste. Um payload de 1 MB virava uma linha de 1 MB no log do
        # CI e congelava o runner do GitHub (diagnostico de 2026-11-12).
        ids=["xxe", "entity_expansion", "malformado"],
    )
    def test_entrada_hostil_recusada(self, xml: bytes, code: str) -> None:
        with pytest.raises(FiscalXmlError) as exc:
            safe_parse(xml)
        assert exc.value.code == code

    def test_entrada_grande_recusada(self) -> None:
        with pytest.raises(FiscalXmlError) as exc:
            safe_parse(b"<x>" + b"a" * 1_000_001 + b"</x>")
        assert exc.value.code == "input_too_large"


class TestProtocolo204:
    def test_fatos_do_retorno(self, package: SchemaPackage) -> None:
        r = analyze_fiscal_xml(PROT_204, package)
        assert r["document_cStat"] == "204" and r["tpAmb"] == "2"
        assert r["access_key_dv_valid"] is True
        assert [f["id"] for f in r["findings"]] == ["F-cStat"]
        assert all(f["kind"] == "fact" for f in r["findings"])
        assert r["signature_validation"] == "not_executed"

    def test_protocolo_validado_por_entrypoint_local(self, package: SchemaPackage) -> None:
        r = analyze_fiscal_xml(PROT_204, package)
        assert r["xsd_validation"] == "valid"
        assert r["xsd"]["entrypoint"] == "protNFe_v4.00.xsd"
        assert r["xsd"]["entrypoint_origin"] == "local_wrapper"

    def test_protocolo_incompleto_e_invalido(self, package: SchemaPackage) -> None:
        sem_dh = re.sub(rb"<dhRecbto>.*?</dhRecbto>", b"", PROT_204)
        r = analyze_fiscal_xml(sem_dh, package)
        assert r["xsd_validation"] == "invalid"
        assert {"F-cStat", "F-xsd"} <= {f["id"] for f in r["findings"]}

    def test_documento_nfe_e_entrypoint_oficial(self, package: SchemaPackage) -> None:
        r = analyze_fiscal_xml(NFE_INCOMPLETA, package)
        assert r["xsd"]["entrypoint_origin"] == "official"

    def test_protocolo_dentro_de_retorno_de_lote(self, package: SchemaPackage) -> None:
        inner = PROT_204.split(b"?>", 1)[1].strip()
        ret = (
            (
                f'<retEnviNFe xmlns="{NS}" versao="4.00"><tpAmb>2</tpAmb><verAplic>DEMO_IIC</verAplic>'
                "<cStat>104</cStat><xMotivo>Lote processado</xMotivo><cUF>35</cUF>"
                "<dhRecbto>2026-11-11T10:00:03-03:00</dhRecbto>"
            ).encode()
            + inner
            + b"</retEnviNFe>"
        )
        r = analyze_fiscal_xml(ret, package)
        assert r["document_cStat"] == "204"  # do protocolo, nunca o 104 do lote
        assert r["protocol_count"] == 1
        assert r["xsd"]["entrypoint"] == "retEnviNFe_v4.00.xsd"

    def test_lote_com_dois_protocolos_nao_escolhe(self, package: SchemaPackage) -> None:
        inner = PROT_204.split(b"?>", 1)[1].strip()
        ret = (
            f'<retConsReciNFe xmlns="{NS}" versao="4.00">'.encode()
            + inner
            + inner
            + b"</retConsReciNFe>"
        )
        r = analyze_fiscal_xml(ret, package)
        assert r.get("document_cStat") is None
        assert "G-lote" in {f["id"] for f in r["findings"]}

    def test_nfeproc_com_protocolo_de_outra_nota(self, package: SchemaPackage) -> None:
        inner = PROT_204.split(b"?>", 1)[1].strip()
        outra = "35261111222333000181550010000000021000000020"
        nfe = f'<NFe xmlns="{NS}"><infNFe versao="4.00" Id="NFe{outra}"/></NFe>'.encode()
        proc = f'<nfeProc xmlns="{NS}" versao="4.00">'.encode() + nfe + inner + b"</nfeProc>"
        r = analyze_fiscal_xml(proc, package)
        assert r["document_cStat"] == "204"
        assert "F-protkey" in {f["id"] for f in r["findings"]}

    def test_hash_de_entrypoint_local_conferido(self, tmp_path) -> None:
        base = tmp_path / "nfe"
        shutil.copytree(SCHEMAS_DIR, base)
        (base / "PL_010g_v1.03" / "Local" / "protNFe_v4.00.xsd").write_text("<x/>")
        with pytest.raises(FiscalXmlError) as exc:
            SchemaPackage.load(base=base)
        assert exc.value.code == "schema_hash_mismatch"

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
        with pytest.raises(FiscalXmlError) as exc:
            validate_xsd(safe_parse(NFE_INCOMPLETA), remoto)
        assert exc.value.code == "schema_remote_forbidden"


FIXTURE_POSITIVO = Path(__file__).parent / "fixtures" / "nfe_controle_positivo_sp.xml"


class TestControlePositivo:
    """NF-e completa derivada do nfelib (MIT), anonimizada, tpAmb=2, SP."""

    def test_nfe_completa_valida_no_pl_010g(self, package: SchemaPackage) -> None:
        r = analyze_fiscal_xml(FIXTURE_POSITIVO.read_bytes(), package)
        assert r["xsd_validation"] == "valid", r["xsd"]["errors"]
        assert r["tpAmb"] == "2"
        assert r["access_key_dv_valid"] is True
        assert r["findings"] == []

    def test_mutacao_controlada_invalida(self, package: SchemaPackage) -> None:
        original = FIXTURE_POSITIVO.read_bytes()
        mutado = original.replace(b"<CNPJ>11222333000181</CNPJ>", b"", 1)
        assert mutado != original
        r = analyze_fiscal_xml(mutado, package)
        assert r["xsd_validation"] == "invalid"
        assert "F-xsd" in [f["id"] for f in r["findings"]]

    def test_assinatura_confere_mas_confianca_nao_verificada(self, package: SchemaPackage) -> None:
        r = analyze_fiscal_xml(FIXTURE_POSITIVO.read_bytes(), package)
        assert r["signature_validation"] == "valid"
        assert r["signature"]["certificate_trust"] == "not_verified"
        assert r["signature"]["certificate"]["icp_cnpj"] == "11222333000181"


# --- P9: assinatura XMLDSig (perfil NF-e, read-only) -------------------------

DS = "http://www.w3.org/2000/09/xmldsig#"


def _assinado(mutate=None, **cert_kw) -> bytes:
    root = safe_parse(FIXTURE_POSITIVO.read_bytes())
    key, cert = make_cert(**cert_kw)
    sign(root, root.find(f"{{{NS}}}infNFe"), key, cert)
    if mutate:
        mutate(root)
    return etree.tostring(root)


def _ids(r: dict) -> set[str]:
    return {f["id"] for f in r["findings"]}


class TestAssinatura:
    def test_conteudo_alterado_apos_assinar(self, package: SchemaPackage) -> None:
        def mut(root):
            root.find(f".//{{{NS}}}natOp").text = "OUTRA NATUREZA"

        r = analyze_fiscal_xml(_assinado(mut), package)
        assert r["signature_validation"] == "invalid"
        assert r["signature"]["reason"] == "digest_mismatch"
        assert "F-sig" in _ids(r)

    def test_signedinfo_alterado(self, package: SchemaPackage) -> None:
        def mut(root):
            sv = root.find(f".//{{{DS}}}SignatureValue")
            sv.text = "AAAA" + sv.text[4:]  # digest intacto, assinatura nao

        r = analyze_fiscal_xml(_assinado(mut), package)
        assert r["signature"]["reason"] == "signature_mismatch"

    def test_algoritmo_fora_do_perfil(self, package: SchemaPackage) -> None:
        def mut(root):
            root.find(f".//{{{DS}}}SignatureMethod").set(
                "Algorithm", "http://www.w3.org/2001/04/xmldsig-more#rsa-md5"
            )

        r = analyze_fiscal_xml(_assinado(mut), package)
        assert r["signature_validation"] == "unsupported"
        assert "G-sig" in _ids(r) and "F-sig" not in _ids(r)

    def test_reference_para_outro_elemento(self, package: SchemaPackage) -> None:
        def mut(root):
            root.find(f".//{{{DS}}}Reference").set("URI", "#outro")

        r = analyze_fiscal_xml(_assinado(mut), package)
        assert r["signature"]["reason"] == "reference_uri"

    def test_xsw_id_duplicado(self, package: SchemaPackage) -> None:
        def mut(root):
            inf = root.find(f"{{{NS}}}infNFe")
            clone = etree.SubElement(root.find(f".//{{{DS}}}KeyInfo"), f"{{{NS}}}infNFe")
            clone.set("Id", inf.get("Id"))

        r = analyze_fiscal_xml(_assinado(mut), package)
        assert r["signature"]["reason"] == "duplicate_id"
        assert r["signature_validation"] != "valid"

    def test_cnpj_do_certificado_diverge(self, package: SchemaPackage) -> None:
        r = analyze_fiscal_xml(_assinado(cnpj="99888777000166"), package)
        assert r["signature_validation"] == "valid"
        assert "F-sigcnpj" in _ids(r)

    def test_emissao_fora_da_validade(self, package: SchemaPackage) -> None:
        import datetime as dt

        r = analyze_fiscal_xml(
            _assinado(
                not_before=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
                not_after=dt.datetime(2021, 1, 1, tzinfo=dt.UTC),
            ),
            package,
        )
        assert "F-sigvalid" in _ids(r)

    def test_sem_assinatura(self, package: SchemaPackage) -> None:
        def mut(root):
            root.remove(root.find(f"{{{DS}}}Signature"))

        r = analyze_fiscal_xml(_assinado(mut), package)
        assert r["signature_validation"] == "not_executed"
        assert "F-xsd" in _ids(r)  # Signature e' obrigatoria no TNFe

    def test_protocolo_assinado(self, package: SchemaPackage) -> None:
        root = safe_parse(PROT_204)
        inf = root.find(f"{{{NS}}}infProt")
        inf.set("Id", "ID135260000000001")
        key, cert = make_cert()
        sign(root, inf, key, cert)
        r = analyze_fiscal_xml(etree.tostring(root), package)
        assert r["signature_validation"] == "valid"
        assert r["xsd_validation"] == "valid"

    def test_protocolo_sem_assinatura_nao_executa(self, package: SchemaPackage) -> None:
        r = analyze_fiscal_xml(PROT_204, package)
        assert r["signature_validation"] == "not_executed"
