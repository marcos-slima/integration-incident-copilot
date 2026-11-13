"""P9: verificacao read-only da assinatura XMLDSig de NF-e.

Perfil fechado (o da NF-e 4.00, MOC 7.0 secao 4): assinatura **irma** do
elemento assinado, uma unica `Reference` com `URI="#<Id>"`, transforms
exatamente `enveloped-signature` + C14N 1.0, `CanonicalizationMethod` C14N
1.0, RSA com SHA-1 (ou SHA-256) e certificado X.509 embutido. Qualquer coisa
fora disso e' `unsupported`, nunca `valid`: um verificador XMLDSig generico
e' superficie de ataque (XSW, transforms arbitrarios, chaves externas).

`valid` significa so que digest e assinatura conferem com o certificado
embutido. NAO verifica cadeia ICP-Brasil, revogacao (LCR/OCSP) nem se o
certificado era o autorizado para o emitente: `certificate_trust` e' sempre
`not_verified`. Nenhuma chamada de rede.
"""

from __future__ import annotations

import base64
import binascii
import re
from datetime import UTC, datetime
from typing import Any, Literal

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from lxml import etree

DS = "http://www.w3.org/2000/09/xmldsig#"
C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
SIGNATURE_METHODS = {
    "http://www.w3.org/2000/09/xmldsig#rsa-sha1": hashes.SHA1,
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256": hashes.SHA256,
}
DIGEST_METHODS = {
    "http://www.w3.org/2000/09/xmldsig#sha1": hashes.SHA1,
    "http://www.w3.org/2001/04/xmlenc#sha256": hashes.SHA256,
}
# ICP-Brasil: CNPJ do titular em otherName do SubjectAltName.
OID_ICP_CNPJ = x509.ObjectIdentifier("2.16.76.1.3.3")

SignatureStatus = Literal["valid", "invalid", "not_executed", "unsupported"]


def _ds(tag: str) -> str:
    return f"{{{DS}}}{tag}"


def _one(parent: etree._Element, tag: str) -> etree._Element | None:
    found = parent.findall(_ds(tag))
    return found[0] if len(found) == 1 else None


def _b64(text: str | None) -> bytes:
    return base64.b64decode(re.sub(r"\s+", "", text or ""), validate=True)


def _digest(alg: type[hashes.HashAlgorithm], data: bytes) -> bytes:
    h = hashes.Hash(alg())
    h.update(data)
    return h.finalize()


def _result(status: SignatureStatus, reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "reason": reason, "certificate_trust": "not_verified", **extra}


def certificate_info(cert: x509.Certificate) -> dict[str, Any]:
    cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    cnpj = None
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        for other in san.get_values_for_type(x509.OtherName):
            if other.type_id == OID_ICP_CNPJ:
                m = re.search(rb"\d{14}", other.value)
                cnpj = m.group().decode() if m else None
    except x509.ExtensionNotFound:
        pass
    return {
        "subject_cn": cn[0].value if cn else None,
        "serial_hex": format(cert.serial_number, "x"),
        "not_before": cert.not_valid_before_utc.isoformat(),
        "not_after": cert.not_valid_after_utc.isoformat(),
        "self_signed": cert.issuer == cert.subject,
        "icp_cnpj": cnpj,
    }


def verify_enveloped(root: etree._Element, signed: etree._Element | None) -> dict[str, Any]:
    """Verifica a `ds:Signature` filha de `root` que assina `signed` (infNFe/infProt)."""
    sigs = root.findall(_ds("Signature"))
    if not sigs:
        return _result("not_executed", "no_signature")
    if len(sigs) > 1:
        return _result("unsupported", "multiple_signatures")
    sig = sigs[0]
    if signed is None or not signed.get("Id"):
        return _result("unsupported", "signed_element_without_id")
    sid = signed.get("Id")
    # XSW: o Id assinado tem de ser unico e ser o elemento que a analise le.
    if len(root.getroottree().xpath("//*[@Id=$i]", i=sid)) != 1:
        return _result("unsupported", "duplicate_id")
    if sig in signed.iterdescendants():
        return _result("unsupported", "signature_inside_signed_element")

    info = _one(sig, "SignedInfo")
    value = _one(sig, "SignatureValue")
    cert_el = sig.find(f"{_ds('KeyInfo')}/{_ds('X509Data')}/{_ds('X509Certificate')}")
    if info is None or value is None or cert_el is None:
        return _result("unsupported", "incomplete_signature")
    c14n_el = _one(info, "CanonicalizationMethod")
    method_el = _one(info, "SignatureMethod")
    refs = info.findall(_ds("Reference"))
    if c14n_el is None or c14n_el.get("Algorithm") != C14N:
        return _result("unsupported", "canonicalization_method")
    sig_alg = SIGNATURE_METHODS.get(method_el.get("Algorithm") if method_el is not None else "")
    if sig_alg is None:
        return _result("unsupported", "signature_method")
    if len(refs) != 1:
        return _result("unsupported", "reference_count")
    ref = refs[0]
    if ref.get("URI") != f"#{sid}":
        return _result("unsupported", "reference_uri")
    transforms = [
        t.get("Algorithm") for t in ref.findall(f"{_ds('Transforms')}/{_ds('Transform')}")
    ]
    if transforms != [ENVELOPED, C14N]:
        return _result("unsupported", "transforms")
    dm = _one(ref, "DigestMethod")
    dig_alg = DIGEST_METHODS.get(dm.get("Algorithm") if dm is not None else "")
    dv = _one(ref, "DigestValue")
    if dig_alg is None or dv is None:
        return _result("unsupported", "digest_method")

    try:
        cert = x509.load_der_x509_certificate(_b64(cert_el.text))
        expected_digest = _b64(dv.text)
        signature = _b64(value.text)
    except (binascii.Error, ValueError):
        return _result("invalid", "malformed_base64_or_certificate")
    key = cert.public_key()
    if not isinstance(key, rsa.RSAPublicKey):
        return _result("unsupported", "key_type")
    info_cert = certificate_info(cert)

    # Assinatura e' irma do elemento assinado: o transform enveloped nao
    # remove nada do subconjunto; a C14N inclusiva leva o xmlns herdado.
    if _digest(dig_alg, etree.tostring(signed, method="c14n")) != expected_digest:
        return _result("invalid", "digest_mismatch", certificate=info_cert)
    try:
        key.verify(signature, etree.tostring(info, method="c14n"), padding.PKCS1v15(), sig_alg())
    except InvalidSignature:
        return _result("invalid", "signature_mismatch", certificate=info_cert)
    return _result("valid", "digest_and_signature_match", certificate=info_cert)


def within_validity(cert: dict[str, Any], when: str | None) -> bool | None:
    """dhEmi dentro da validade do certificado; None se nao der para dizer."""
    if not when:
        return None
    try:
        t = datetime.fromisoformat(when)
    except ValueError:
        return None
    if t.tzinfo is None:
        return None
    t = t.astimezone(UTC)
    return (
        datetime.fromisoformat(cert["not_before"]) <= t <= datetime.fromisoformat(cert["not_after"])
    )
