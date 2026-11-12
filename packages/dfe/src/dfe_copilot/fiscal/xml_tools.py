"""DA-93 (fase 2): parse seguro, chave de acesso e validacao XSD de NF-e.

Regras deste modulo:
- o XML original nunca e' alterado; o hash e' sobre os bytes recebidos;
- DTD, entidades externas e qualquer resolucao fora do pacote local sao
  recusados (XXE, entity expansion, schema remoto);
- sem pacote aplicavel, `xsd_validation="not_executed"` - nunca "valido";
- `well_formed` nao implica conformidade XSD, assinatura integra,
  regra fiscal satisfeita nem autorizacao.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from lxml import etree

NS = "http://www.portalfiscal.inf.br/nfe"
MAX_XML_BYTES = 1_000_000
SCHEMAS_DIR = Path(__file__).resolve().parents[3] / "data" / "schemas" / "nfe"
DEFAULT_PACKAGE = "PL_010g_v1.03"

XsdStatus = Literal["valid", "invalid", "not_executed"]

_UF_IBGE = {
    "11", "12", "13", "14", "15", "16", "17", "21", "22", "23", "24", "25", "26", "27", "28",
    "29", "31", "32", "33", "35", "41", "42", "43", "50", "51", "52", "53",
}  # fmt: skip


class FiscalXmlError(ValueError):
    """Entrada recusada antes de qualquer interpretacao (codigo estavel em `code`)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class _LocalOnlyResolver(etree.Resolver):
    """Recusa qualquer URL de rede; includes relativos resolvem no disco do pacote."""

    def resolve(self, url, pubid, context):
        if (
            url
            and url.split(":", 1)[0].lower() in {"http", "https", "ftp", "file"}
            and "://" in url
        ):
            raise FiscalXmlError("schema_remote_forbidden", url)


def _parser() -> etree.XMLParser:
    parser = etree.XMLParser(
        resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False, recover=False
    )
    parser.resolvers.add(_LocalOnlyResolver())
    return parser


def safe_parse(xml_bytes: bytes) -> etree._Element:
    if len(xml_bytes) > MAX_XML_BYTES:
        raise FiscalXmlError("input_too_large", f"{len(xml_bytes)} bytes")
    try:
        root = etree.fromstring(xml_bytes, _parser())
    except etree.XMLSyntaxError as exc:
        raise FiscalXmlError("malformed_xml", str(exc)) from exc
    if root.getroottree().docinfo.doctype:
        raise FiscalXmlError("dtd_forbidden")
    return root


@dataclass(frozen=True)
class AccessKey:
    raw: str
    dv_valid: bool
    cuf: str
    aamm: str
    cnpj_cpf: str
    model: str
    serie: str
    number: str
    tp_emis: str
    cnf: str
    dv: str


def _mod11_dv(body: str) -> str:
    total, weight = 0, 2
    for c in reversed(body):
        total += int(c) * weight
        weight = weight + 1 if weight < 9 else 2
    rest = total % 11
    return "0" if rest < 2 else str(11 - rest)


def parse_access_key(key: str | None) -> AccessKey | None:
    """Decompoe a chave de 44 digitos; None se o formato nao for de chave."""
    if not key or len(key) != 44 or not key.isdigit():
        return None
    dv_ok = key[43] == _mod11_dv(key[:43]) and key[:2] in _UF_IBGE
    return AccessKey(
        raw=key, dv_valid=dv_ok, cuf=key[0:2], aamm=key[2:6], cnpj_cpf=key[6:20],
        model=key[20:22], serie=key[22:25], number=key[25:34], tp_emis=key[34],
        cnf=key[35:43], dv=key[43],
    )  # fmt: skip


@dataclass(frozen=True)
class SchemaPackage:
    package_id: str
    manifest: dict[str, Any]
    root: Path

    @classmethod
    def load(cls, package_id: str = DEFAULT_PACKAGE, base: Path = SCHEMAS_DIR) -> SchemaPackage:
        manifest_path = base / f"{package_id}.manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        root = base / package_id
        for rel, expected in manifest["files_sha256"].items():
            got = hashlib.sha256((root / rel).read_bytes()).hexdigest()
            if got != expected:
                raise FiscalXmlError("schema_hash_mismatch", rel)
        return cls(package_id=package_id, manifest=manifest, root=root)

    def entrypoint(self, element: str, version: str) -> Path | None:
        rel = self.manifest.get("entrypoints", {}).get(f"{element}@{version}")
        return self.root / rel if rel else None


@lru_cache(maxsize=8)
def _compiled(path: str) -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(path, _parser()))


def validate_xsd(root: etree._Element, package: SchemaPackage | None) -> dict[str, Any]:
    """Valida o documento contra o entrypoint do pacote; sem pacote/entrypoint: not_executed."""
    local = etree.QName(root).localname
    namespace = etree.QName(root).namespace
    version = root.get("versao") or next(
        (c.get("versao") for c in root if isinstance(c.tag, str) and c.get("versao")), None
    )
    base: dict[str, Any] = {"package_id": package.package_id if package else None, "errors": []}
    if package is None or namespace != NS or version is None:
        return {**base, "status": "not_executed", "reason": "package_or_document_unknown"}
    entry = package.entrypoint(local, version)
    if entry is None:
        return {**base, "status": "not_executed", "reason": f"no_entrypoint:{local}@{version}"}
    schema = _compiled(str(entry))
    ok = schema.validate(root)
    errors = [
        {"line": e.line, "path": e.path, "message": e.message} for e in list(schema.error_log)[:20]
    ]
    return {
        **base,
        "status": "valid" if ok else "invalid",
        "entrypoint": entry.name,
        "errors": errors,
    }


def _text(root: etree._Element, *path: str) -> str | None:
    xpath = "/".join(f"{{{NS}}}{p}" for p in path)
    found = root.findall(xpath)
    return found[0].text if len(found) == 1 else None


def analyze_fiscal_xml(xml_bytes: bytes, package: SchemaPackage | None = None) -> dict[str, Any]:
    """Fatos deterministicos sobre um XML NF-e (documento ou protocolo).

    Le `cStat` somente em `protNFe/infProt` - nunca o status de lote.
    Levanta FiscalXmlError para entrada recusada (DTD, malformado, grande).
    """
    root = safe_parse(xml_bytes)
    qname = etree.QName(root)
    fiscal_ns = qname.namespace == NS
    out: dict[str, Any] = {
        "raw_sha256": hashlib.sha256(xml_bytes).hexdigest(),
        "well_formed": True,
        "fiscal_namespace": fiscal_ns,
        "root_element": qname.localname,
        "signature_validation": "not_executed",
        "findings": [],
    }
    key: str | None = None
    if fiscal_ns and qname.localname == "protNFe":
        out["document_cStat"] = _text(root, "infProt", "cStat")
        out["xMotivo"] = _text(root, "infProt", "xMotivo")
        out["tpAmb"] = _text(root, "infProt", "tpAmb")
        key = _text(root, "infProt", "chNFe")
    elif fiscal_ns and qname.localname == "NFe":
        inf = root.find(f"{{{NS}}}infNFe")
        node_id = inf.get("Id") if inf is not None else None
        key = node_id[3:] if node_id and node_id.startswith("NFe") else None
        out["tpAmb"] = _text(root, "infNFe", "ide", "tpAmb")

    parsed = parse_access_key(key)
    out["access_key"] = parsed.raw if parsed else None
    out["access_key_dv_valid"] = parsed.dv_valid if parsed else None

    xsd = validate_xsd(root, package)
    out["xsd_validation"] = xsd["status"]
    out["xsd"] = xsd

    if out.get("document_cStat"):
        out["findings"].append({
            "id": "F-cStat", "kind": "fact", "source": "tool",
            "statement": f"Retorno do documento com cStat {out['document_cStat']} (tpAmb={out.get('tpAmb')}).",
            "evidence_refs": [f"sha256:{out['raw_sha256']}"],
        })  # fmt: skip
    if parsed is not None and not parsed.dv_valid:
        out["findings"].append({
            "id": "F-dv", "kind": "fact", "source": "tool",
            "statement": "Chave de acesso com digito verificador ou cUF invalido.",
            "evidence_refs": [f"sha256:{out['raw_sha256']}"],
        })  # fmt: skip
    if xsd["status"] == "invalid":
        out["findings"].append({
            "id": "F-xsd", "kind": "fact", "source": "tool",
            "statement": f"XML nao conforme ao {xsd.get('entrypoint')} ({xsd['package_id']}).",
            "evidence_refs": [f"sha256:{out['raw_sha256']}"],
        })  # fmt: skip
    return out
