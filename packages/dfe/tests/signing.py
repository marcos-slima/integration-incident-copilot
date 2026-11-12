"""Assinador de TESTE no perfil NF-e (P9). So para gerar fixtures sinteticos:
chave efemera, certificado autoassinado com CNPJ ficticio. Nunca usar com
certificado real."""

from __future__ import annotations

import base64
import datetime as dt

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from dfe_copilot.fiscal.signature import C14N, DS, ENVELOPED, OID_ICP_CNPJ
from lxml import etree

TEMPLATE = f"""<Signature xmlns="{DS}"><SignedInfo>
<CanonicalizationMethod Algorithm="{C14N}"/>
<SignatureMethod Algorithm="http://www.w3.org/2000/09/xmldsig#rsa-sha1"/>
<Reference URI=""><Transforms><Transform Algorithm="{ENVELOPED}"/><Transform Algorithm="{C14N}"/></Transforms>
<DigestMethod Algorithm="http://www.w3.org/2000/09/xmldsig#sha1"/><DigestValue/></Reference>
</SignedInfo><SignatureValue/><KeyInfo><X509Data><X509Certificate/></X509Data></KeyInfo></Signature>"""


def make_cert(cnpj: str = "11222333000181", not_before=None, not_after=None):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, f"EMPRESA FICTICIA:{cnpj}")])
    nb = not_before or dt.datetime(2018, 1, 1, tzinfo=dt.UTC)
    na = not_after or dt.datetime(2030, 1, 1, tzinfo=dt.UTC)
    san = x509.SubjectAlternativeName([x509.OtherName(OID_ICP_CNPJ, b"\x04\x0e" + cnpj.encode())])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(nb)
        .not_valid_after(na)
        .add_extension(san, critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _ds(tag: str) -> str:
    return f"{{{DS}}}{tag}"


def sign(parent: etree._Element, signed: etree._Element, key, cert) -> None:
    """Preenche (ou cria) a ds:Signature filha de `parent` assinando `signed`."""
    sig = parent.find(_ds("Signature"))
    if sig is None:
        sig = etree.fromstring(TEMPLATE)
        parent.append(sig)
    info = sig.find(_ds("SignedInfo"))
    ref = info.find(_ds("Reference"))
    ref.set("URI", f"#{signed.get('Id')}")
    h = hashes.Hash(hashes.SHA1())
    h.update(etree.tostring(signed, method="c14n"))
    ref.find(_ds("DigestValue")).text = base64.b64encode(h.finalize()).decode()
    der = cert.public_bytes(serialization.Encoding.DER)
    sig.find(
        f"{_ds('KeyInfo')}/{_ds('X509Data')}/{_ds('X509Certificate')}"
    ).text = base64.b64encode(der).decode()
    value = key.sign(etree.tostring(info, method="c14n"), padding.PKCS1v15(), hashes.SHA1())
    sig.find(_ds("SignatureValue")).text = base64.b64encode(value).decode()
