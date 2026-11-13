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
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from lxml import etree

from dfe_copilot.fiscal.cstat import COM_PROTOCOLO, TP_AMB
from dfe_copilot.fiscal.cstat import describe as describe_cstat
from dfe_copilot.fiscal.signature import verify_enveloped, within_validity

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
        # P5: entrypoints locais (nao oficiais) tambem tem hash conferido.
        for rel, meta in manifest.get("local_wrappers", {}).items():
            got = hashlib.sha256((root / rel).read_bytes()).hexdigest()
            if got != meta["sha256"]:
                raise FiscalXmlError("schema_hash_mismatch", rel)
        return cls(package_id=package_id, manifest=manifest, root=root)

    def entrypoint(self, element: str, version: str) -> Path | None:
        rel = self.manifest.get("entrypoints", {}).get(f"{element}@{version}")
        return self.root / rel if rel else None

    def entrypoint_origin(self, element: str, version: str) -> str | None:
        """`official` (arquivo do ZIP) ou `local_wrapper` (P5: so declara o
        elemento global com o tipo oficial do leiaute)."""
        rel = self.manifest.get("entrypoints", {}).get(f"{element}@{version}")
        if rel is None:
            return None
        return "local_wrapper" if rel in self.manifest.get("local_wrappers", {}) else "official"


_REMOTE_LOCATION = re.compile(rb'schemaLocation\s*=\s*["\'](?:https?|ftp|file):', re.IGNORECASE)


def _assert_local_only(entry: Path) -> None:
    """Recusa ANTES de compilar qualquer XSD do pacote que aponte para fora.

    O resolver da lxml nao cobre todos os caminhos de include/import do
    compilador de schema; sem esta checagem a libxml2 pode tentar a rede.
    """
    for xsd in sorted(entry.parent.parent.rglob("*.xsd")) or [entry]:
        if _REMOTE_LOCATION.search(xsd.read_bytes()):
            raise FiscalXmlError("schema_remote_forbidden", xsd.name)


@lru_cache(maxsize=8)
def _compiled(path: str) -> etree.XMLSchema:
    _assert_local_only(Path(path))
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
        "entrypoint_origin": package.entrypoint_origin(local, version),
        "errors": errors,
    }


def _text(root: etree._Element, *path: str) -> str | None:
    xpath = "/".join(f"{{{NS}}}{p}" for p in path)
    found = root.findall(xpath)
    return found[0].text if len(found) == 1 else None


# Raizes que carregam protocolo de NF-e (P5).
_PROTOCOL_CONTAINERS = ("protNFe", "nfeProc", "retEnviNFe", "retConsReciNFe")


def _protocol(root: etree._Element) -> etree._Element | None:
    """O `protNFe` do XML: a propria raiz, ou o unico filho `protNFe` de um
    contêiner. Lote com 2+ protocolos devolve None (nao escolhe um)."""
    local = etree.QName(root).localname
    if local == "protNFe":
        return root
    if local in _PROTOCOL_CONTAINERS:
        found = root.findall(f"{{{NS}}}protNFe")
        return found[0] if len(found) == 1 else None
    return None


def _signature_target(
    root: etree._Element, prot: etree._Element | None
) -> tuple[etree._Element, etree._Element | None] | None:
    """(pai da ds:Signature, elemento assinado). A NF-e tem prioridade sobre o
    protocolo quando os dois estao no arquivo (nfeProc)."""
    local = etree.QName(root).localname
    nfe = root if local == "NFe" else root.find(f"{{{NS}}}NFe") if local == "nfeProc" else None
    if nfe is not None:
        return nfe, nfe.find(f"{{{NS}}}infNFe")
    if prot is not None:
        return prot, prot.find(f"{{{NS}}}infProt")
    return None


def _check_signature(
    root: etree._Element, prot: etree._Element | None, out: dict[str, Any], key: str | None
) -> None:
    """P9: assinatura XMLDSig, perfil NF-e, read-only (dfe_copilot.fiscal.signature)."""
    if etree.QName(root).namespace != NS:
        return
    target = _signature_target(root, prot)
    if target is None:
        return
    result = verify_enveloped(*target)
    out["signature_validation"] = result["status"]
    out["signature"] = result
    ref = [f"sha256:{out['raw_sha256']}"]
    if result["status"] == "invalid":
        out["findings"].append({
            "id": "F-sig", "kind": "fact", "source": "tool",
            "statement": f"Assinatura XMLDSig nao confere com o certificado embutido ({result['reason']}).",
            "evidence_refs": ref,
        })  # fmt: skip
    elif result["status"] == "unsupported":
        out["findings"].append({
            "id": "G-sig", "kind": "fact", "source": "tool",
            "statement": f"Assinatura fora do perfil NF-e ({result['reason']}); nao verificada.",
            "evidence_refs": ref,
        })  # fmt: skip
    elif result["status"] == "valid":
        cert = result["certificate"]
        signer, doc_cnpj = cert.get("icp_cnpj"), (key or "")[6:20] or None
        if (
            signer
            and doc_cnpj
            and signer != doc_cnpj
            and target[1] is not None
            and (etree.QName(target[1]).localname == "infNFe")
        ):
            out["findings"].append({
                "id": "F-sigcnpj", "kind": "fact", "source": "tool",
                "statement": "CNPJ do certificado (ICP-Brasil) difere do CNPJ emitente da chave de acesso.",
                "evidence_refs": ref,
            })  # fmt: skip
        dh = target[1].findtext(f"{{{NS}}}ide/{{{NS}}}dhEmi") if target[1] is not None else None
        if within_validity(cert, dh) is False:
            out["findings"].append({
                "id": "F-sigvalid", "kind": "fact", "source": "tool",
                "statement": "dhEmi fora da validade do certificado que assinou a NF-e.",
                "evidence_refs": ref,
            })  # fmt: skip


def analyze_fiscal_xml(xml_bytes: bytes, package: SchemaPackage | None = None) -> dict[str, Any]:
    """Fatos deterministicos sobre um XML NF-e (documento ou protocolo).

    Le `cStat` somente em `protNFe/infProt` - nunca o status de lote. O
    protocolo pode vir sozinho ou dentro de `nfeProc`, `retEnviNFe` ou
    `retConsReciNFe` (P5); com mais de um protocolo no lote, nenhum e' escolhido.
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
    if not fiscal_ns:  # P7: XML que nao e' NF-e nao passa calado
        out["findings"].append({
            "id": "G-ns", "kind": "fact", "source": "tool",
            "statement": f"XML fora do namespace da NF-e (raiz '{qname.localname}'); nenhuma analise fiscal.",
            "evidence_refs": [f"sha256:{out['raw_sha256']}"],
        })  # fmt: skip
    prot = _protocol(root) if fiscal_ns else None
    if fiscal_ns and qname.localname in _PROTOCOL_CONTAINERS:
        out["protocol_count"] = (
            1 if qname.localname == "protNFe" else len(root.findall(f"{{{NS}}}protNFe"))
        )
    if prot is not None:
        out["document_cStat"] = _text(prot, "infProt", "cStat")
        out["xMotivo"] = _text(prot, "infProt", "xMotivo")
        out["tpAmb"] = _text(prot, "infProt", "tpAmb")
        out["nProt"] = _text(prot, "infProt", "nProt")
        key = _text(prot, "infProt", "chNFe")
    elif fiscal_ns and qname.localname == "NFe":
        inf = root.find(f"{{{NS}}}infNFe")
        node_id = inf.get("Id") if inf is not None else None
        key = node_id[3:] if node_id and node_id.startswith("NFe") else None
        out["tpAmb"] = _text(root, "infNFe", "ide", "tpAmb")

    if out.get("protocol_count", 0) > 1:
        out["findings"].append({
            "id": "G-lote", "kind": "fact", "source": "tool",
            "statement": f"Retorno com {out['protocol_count']} protocolos; cStat por documento nao escolhido.",
            "evidence_refs": [f"sha256:{out['raw_sha256']}"],
        })  # fmt: skip
    if fiscal_ns and qname.localname == "nfeProc" and prot is not None:
        inf = root.find(f"{{{NS}}}NFe/{{{NS}}}infNFe")
        node_id = inf.get("Id") if inf is not None else None
        doc_key = node_id[3:] if node_id and node_id.startswith("NFe") else None
        if doc_key and key and doc_key != key:
            out["findings"].append({
                "id": "F-protkey", "kind": "fact", "source": "tool",
                "statement": "Protocolo do nfeProc nao pertence a NF-e do mesmo arquivo (chNFe difere do Id).",
                "evidence_refs": [f"sha256:{out['raw_sha256']}"],
            })  # fmt: skip

    parsed = parse_access_key(key)
    out["access_key"] = parsed.raw if parsed else None
    out["access_key_dv_valid"] = parsed.dv_valid if parsed else None

    xsd = validate_xsd(root, package)
    out["xsd_validation"] = xsd["status"]
    out["xsd"] = xsd

    if out.get("document_cStat"):
        c = out["document_cStat"]
        info = describe_cstat(c)
        out["cstat_official"] = info
        amb = TP_AMB.get(out.get("tpAmb") or "", "desconhecido")
        oficial = (
            f' - {info["source_id"]} ({info["table"]}, p. {info["page"]}): "{info["text"]}"'
            if info
            else " - codigo ausente da tabela oficial carregada"
        )
        out["findings"].append({
            "id": "F-cStat", "kind": "fact", "source": "tool",
            "statement": f"Retorno do documento com cStat {c}{oficial}; tpAmb={out.get('tpAmb')} ({amb}).",
            "evidence_refs": [f"sha256:{out['raw_sha256']}"],
        })  # fmt: skip
        outra_nota = any(f["id"] == "F-protkey" for f in out["findings"])
        if c in COM_PROTOCOLO and out.get("nProt") and not outra_nota:
            resultado = "autorizacao de uso" if c in ("100", "150") else "denegacao de uso"
            out["findings"].append({
                "id": "F-prot", "kind": "fact", "source": "tool",
                "statement": (
                    f"Protocolo nProt {out['nProt']} presente no XML: o proprio retorno comprova a "
                    f"{resultado} (cStat {c}); nao e' preciso consultar para saber o desfecho."
                ),
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
            "statement": f"XML nao conforme ao {xsd.get('entrypoint')} ({xsd['package_id']}, {xsd.get('entrypoint_origin')}).",
            "evidence_refs": [f"sha256:{out['raw_sha256']}"],
        })  # fmt: skip
    _check_signature(root, prot, out, key)  # depois do XSD: estrutura antes de integridade
    return out
