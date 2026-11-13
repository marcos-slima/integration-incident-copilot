"""Gera o dataset sintetico v1 do DF-e Copilot (100 casos, gabarito NAO revisado).

Cada caso traz entrada (payload, domain_context) e o gabarito esperado das
ferramentas deterministicas (`expected_finding_ids`). O gabarito e' derivado
das regras implementadas; a revisao fiscal humana (P7) e' pendente: cada caso
nasce com `reviewed=false` e so o revisor muda isso (docs/REVISAO_DATASET.md).

Os 29 primeiros casos sao os do v0 (ids e entradas inalterados). `split` e'
deterministico pelo case_id: dev (ajuste de regra/prompt), validation
(conferencia) e blind (nunca olhado durante ajuste).

Uso: uv run python packages/dfe/scripts/gen_dataset.py  (regrava data/eval/cases_v1.jsonl)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataset_texts import DESCRICAO, JUSTIFICATIVA

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "eval" / "cases_v1.jsonl"
REVISAO = ROOT / "data" / "eval" / "revisao_v1.yaml"
FIXTURES = ROOT / "tests" / "fixtures"
POSITIVO = (ROOT / "tests" / "fixtures" / "nfe_controle_positivo_sp.xml").read_text(
    encoding="utf-8"
)


def _sem_comentario(xml: str) -> str:
    """Tira o comentario de procedencia do fixture: e' metadado de teste, e o
    LLM o lia como fato ("certificado ficticio") na medicao de 2026-11-12."""
    return re.sub(r"<!--.*?-->\s*", "", xml, count=1, flags=re.DOTALL)


POSITIVO = _sem_comentario(POSITIVO)
VARIANTE_CNPJ = _sem_comentario(
    (FIXTURES / "nfe_assinada_cnpj_divergente.xml").read_text(encoding="utf-8")
)
VARIANTE_VENCIDO = _sem_comentario(
    (FIXTURES / "nfe_assinada_cert_vencido.xml").read_text(encoding="utf-8")
)
NS = "http://www.portalfiscal.inf.br/nfe"
DS = "http://www.w3.org/2000/09/xmldsig#"
COM_NPROT = ("100", "150", "110", "301", "302")  # protocolo com nProt (autorizacao/denegacao)
CNPJ = "11222333000181"  # ficticio


def _dv(body: str) -> str:
    total, w = 0, 2
    for c in reversed(body):
        total += int(c) * w
        w = w + 1 if w < 9 else 2
    r = total % 11
    return "0" if r < 2 else str(11 - r)


def chave_cnf(n: int, cnf: int, cuf: str = "35") -> str:
    """Mesma serie/numero com outro cNF: o que um reenvio com chave regerada produz."""
    body = f"{cuf}2611{CNPJ}55001{n:09d}1{cnf:08d}"
    return body + _dv(body)


def chave(n: int, cuf: str = "35") -> str:
    body = f"{cuf}2611{CNPJ}55001{n:09d}1{n:08d}"
    return body + _dv(body)


def prot(key: str, cstat: str, motivo: str, tp_amb: str = "2", ns: bool = True) -> str:
    """Protocolo sintetico; autorizacao/denegacao trazem nProt, como no real."""
    xmlns = f' xmlns="{NS}"' if ns else ""
    nprot = f"<nProt>1352600000{key[-5:]}</nProt>" if cstat in COM_NPROT else ""
    return (
        f'<protNFe{xmlns} versao="4.00"><infProt><tpAmb>{tp_amb}</tpAmb>'
        f"<verAplic>DEMO_IIC</verAplic><chNFe>{key}</chNFe>"
        f"<dhRecbto>2026-11-11T09:52:00-03:00</dhRecbto>{nprot}"
        f"<cStat>{cstat}</cStat><xMotivo>{motivo}</xMotivo></infProt></protNFe>"
    )


def ret(root: str, *prots: str, lote: str = "104") -> str:
    """Retorno de lote (retEnviNFe/retConsReciNFe) com protocolos dentro."""
    head = (
        f'<{root} xmlns="{NS}" versao="4.00"><tpAmb>2</tpAmb><verAplic>DEMO_IIC</verAplic>'
        f"<cStat>{lote}</cStat><xMotivo>Lote processado</xMotivo><cUF>35</cUF>"
        "<dhRecbto>2026-11-11T09:52:00-03:00</dhRecbto>"
    )
    if root == "retConsReciNFe":
        head = head.replace("<cStat>", "<nRec>351000000000001</nRec><cStat>")
    inner = "".join(p.replace(f' xmlns="{NS}"', "") for p in prots)
    return head + inner + f"</{root}>"


def nfe_proc(nfe_xml: str, protocolo: str) -> str:
    corpo = nfe_xml.split("?>", 1)[1]
    corpo = corpo[corpo.index("<NFe") :].strip()
    return (
        f'<nfeProc xmlns="{NS}" versao="4.00">'
        + corpo
        + protocolo.replace(f' xmlns="{NS}"', "")
        + "</nfeProc>"
    )


def chave_do_positivo() -> str:
    i = POSITIVO.index('Id="NFe') + 7
    return POSITIVO[i : i + 44]


def split_de(case_id: str) -> str:
    import hashlib

    b = int(hashlib.sha256(case_id.encode()).hexdigest(), 16) % 10
    return "dev" if b < 6 else "validation" if b < 8 else "blind"


def ev(eid: str, key: str, at: str, kind: str) -> dict:
    return {"evidence_id": eid, "document_ref": key, "at": at, "event": kind}


def _revisao() -> dict[str, dict]:
    import yaml

    if not REVISAO.exists():
        return {}
    return (yaml.safe_load(REVISAO.read_text(encoding="utf-8")) or {}).get("casos") or {}


def casos() -> list[dict]:
    out: list[dict] = []
    revisao = _revisao()

    def add(
        cid: str,
        scenario: str,
        payload: str | None,
        events: list,
        expected: list[str],
        env="homologation",
    ):
        out.append({
            "case_id": cid, "scenario": scenario, "synthetic": True,
            "reviewed": (revisao.get(cid) or {}).get("status") == "aprovado",
            "split": split_de(cid),
            "description": DESCRICAO[scenario.split("_")[0]],
            "rationale": JUSTIFICATIVA[scenario],
            "payload": payload,
            "domain_context": {"environment": env, "jurisdiction": "SP", "events": events},
            "expected_finding_ids": expected,
        })  # fmt: skip

    for i in range(1, 6):  # UC01: 204 com reenvio apos timeout
        k = chave(100 + i)
        add(f"UC01-H1-{i:02d}", "uc01_204_reenvio", prot(k, "204", "Rejeicao: Duplicidade de NF-e"),
            [ev("A", k, f"2026-11-11T12:{50 + i}:00Z", "sent"), ev("T", k, f"2026-11-11T12:{50 + i}:30Z", "timeout"),
             ev("B", k, f"2026-11-11T12:{50 + i}:31Z", "sent")], ["F-cStat", "F-204", "H1"])  # fmt: skip
    for i in range(1, 4):  # UC01 controles negativos
        k = chave(200 + i)
        p = prot(k, "204", "Rejeicao: Duplicidade de NF-e")
        add(f"UC01-SEMTO-{i:02d}", "uc01_204_sem_timeout", p,
            [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("B", k, "2026-11-11T12:00:05Z", "sent")],
            ["F-cStat", "F-204", "G-204"])  # fmt: skip
        add(f"UC01-TARDE-{i:02d}", "uc01_204_timeout_tardio", p,
            [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("B", k, "2026-11-11T12:00:05Z", "sent"),
             ev("T", k, "2026-11-11T12:10:00Z", "timeout")], ["F-cStat", "F-204", "G-204"])  # fmt: skip
        k2 = chave(300 + i)
        add(f"UC01-DOCS-{i:02d}", "uc01_204_documentos_distintos", p,
            [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("T", k, "2026-11-11T12:00:30Z", "timeout"),
             ev("B", k2, "2026-11-11T12:00:31Z", "sent")], ["F-cStat", "F-204", "G-204"])  # fmt: skip
    for i in range(1, 4):  # UC02: timeout sem retorno / com resposta
        k = chave(400 + i)
        add(f"UC02-UNK-{i:02d}", "uc02_timeout_sem_retorno", None,
            [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("T", k, "2026-11-11T12:01:00Z", "timeout")],
            ["G-noxml", "F-timeout", "H-timeout"])  # fmt: skip
        add(f"UC02-RESP-{i:02d}", "uc02_timeout_com_resposta", None,
            [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("T", k, "2026-11-11T12:01:00Z", "timeout"),
             ev("R", k, "2026-11-11T12:01:20Z", "response")], ["G-noxml", "F-timeout"])  # fmt: skip
    # UC03: XSD
    add("UC03-VALIDO-01", "uc03_controle_positivo", POSITIVO, [], [])
    for i, (alvo, novo) in enumerate(
        [(f"<CNPJ>{CNPJ}</CNPJ>", ""), ("<natOp>", "<natOpX>"), ("<mod>55</mod>", "<mod>99</mod>")],
        start=1,
    ):
        mut = POSITIVO.replace(alvo, novo, 1)
        if alvo == "<natOp>":
            mut = mut.replace("</natOp>", "</natOpX>", 1)
        add(
            f"UC03-MUT-{i:02d}", "uc03_mutacao_xsd", mut, [], ["F-xsd", "F-sig"]
        )  # alterar o infNFe quebra o digest (P9)
    add("UC03-AMB-01", "uc03_homologacao_em_producao", POSITIVO, [], ["F-amb"], env="production")
    # Seguranca / lacunas
    add(
        "SEC-XXE-01",
        "sec_xxe",
        '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',
        [],
        ["G-xml"],
    )
    add("SEC-MAL-01", "sec_malformado", "<protNFe>", [], ["G-xml"])
    add("GAP-DV-01", "gap_dv_invalido", prot(chave(500)[:43] + "0" if chave(500)[43] != "0" else chave(500)[:43] + "1",
        "204", "Rejeicao: Duplicidade de NF-e"), [], ["F-cStat", "F-dv"])  # fmt: skip
    add("GAP-TZ-01", "gap_evento_sem_fuso", None,
        [{"evidence_id": "A", "document_ref": chave(600), "at": "2026-11-11T12:00:00", "event": "sent"}],
        ["G-noxml", "G-events"])  # fmt: skip
    _casos_v1(add)
    return out


def _casos_v1(add) -> None:
    dup = "Rejeicao: Duplicidade de NF-e"
    # UC01: mais formas de H1
    for i in range(1, 4):  # tres envios, dois timeouts
        k = chave(700 + i)
        add(f"UC01-H1MULT-{i:02d}", "uc01_204_reenvio_multiplo", prot(k, "204", dup),
            [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("T1", k, "2026-11-11T12:00:30Z", "timeout"),
             ev("B", k, "2026-11-11T12:00:31Z", "sent"), ev("T2", k, "2026-11-11T12:01:01Z", "timeout"),
             ev("C", k, "2026-11-11T12:01:02Z", "sent")], ["F-cStat", "F-204", "H1"])  # fmt: skip
    for i in range(1, 4):  # horarios com fuso -03:00 (BRT) misturados com Z
        k = chave(710 + i)
        add(f"UC01-H1TZ-{i:02d}", "uc01_204_reenvio_fuso_local", prot(k, "204", dup),
            [ev("A", k, "2026-11-11T09:00:00-03:00", "sent"), ev("T", k, "2026-11-11T12:00:30Z", "timeout"),
             ev("B", k, "2026-11-11T09:00:31-03:00", "sent")], ["F-cStat", "F-204", "H1"])  # fmt: skip
    for i in range(1, 4):  # protocolo dentro do retorno de lote
        k = chave(720 + i)
        add(f"UC01-RET-{i:02d}", "uc01_204_em_retorno_de_lote", ret("retEnviNFe", prot(k, "204", dup)),
            [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("T", k, "2026-11-11T12:00:30Z", "timeout"),
             ev("B", k, "2026-11-11T12:00:31Z", "sent")], ["F-cStat", "F-204", "H1"])  # fmt: skip
    for i in range(1, 4):  # producao (tpAmb=1) em contexto de producao
        k = chave(730 + i)
        add(f"UC01-PROD-{i:02d}", "uc01_204_reenvio_producao", prot(k, "204", dup, tp_amb="1"),
            [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("T", k, "2026-11-11T12:00:30Z", "timeout"),
             ev("B", k, "2026-11-11T12:00:31Z", "sent")], ["F-cStat", "F-204", "H1"], env="production")  # fmt: skip
    k = chave(740)  # timeout no MESMO instante do reenvio: conta (revisao P7, `<=`)
    add("UC01-BORDA-01", "uc01_204_timeout_simultaneo", prot(k, "204", dup),
        [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("T", k, "2026-11-11T12:00:30Z", "timeout"),
         ev("B", k, "2026-11-11T12:00:30Z", "sent")], ["F-cStat", "F-204", "H1"])  # fmt: skip
    k = chave(741)  # um unico envio com timeout: sem reenvio
    add("UC01-BORDA-02", "uc01_204_envio_unico", prot(k, "204", dup),
        [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("T", k, "2026-11-11T12:00:30Z", "timeout")],
        ["F-cStat", "F-204", "G-204"])  # fmt: skip
    for i in range(1, 3):  # 204 com protocolo fora do XSD (sem dhRecbto)
        k = chave(750 + i)
        p = prot(k, "204", dup).replace("<dhRecbto>2026-11-11T09:52:00-03:00</dhRecbto>", "")
        add(f"UC01-XSD-{i:02d}", "uc01_204_protocolo_fora_do_xsd", p, [],
            ["F-cStat", "F-xsd", "F-204", "G-204"])  # fmt: skip

    # UC04: outros cStat (sem regra local: so o fato; a explicacao vem da base/LLM)
    outros = [("100", "Autorizado o uso da NF-e"), ("110", "Uso Denegado"),
              ("150", "Autorizado o uso da NF-e, autorizacao fora de prazo"),
              ("215", "Rejeicao: Falha no schema XML"), ("225", "Rejeicao: Falha no Schema XML da NFe"),
              ("297", "Rejeicao: Assinatura difere do calculado"),
              ("301", "Uso Denegado: Irregularidade fiscal do emitente"),
              ("302", "Uso Denegado: Irregularidade fiscal do destinatario"),
              ("539", "Rejeicao: Duplicidade de NF-e com diferenca na Chave de Acesso"),
              ("999", "Rejeicao: Erro nao catalogado")]  # fmt: skip
    for i, (c, m) in enumerate(outros, start=1):
        add(f"UC04-CSTAT-{i:02d}", f"uc04_cstat_{c}", prot(chave(800 + i), c, m), [],
            ["F-cStat"] + (["F-prot"] if c in COM_NPROT else []) + (["G-539"] if c == "539" else []))  # fmt: skip
    for i, (c, m) in enumerate(outros[:5], start=1):  # retorno presente: timeout nao vira H-timeout
        k = chave(820 + i)
        add(f"UC04-COMTO-{i:02d}", f"uc04_cstat_{c}_com_timeout", prot(k, c, m),
            [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("T", k, "2026-11-11T12:00:30Z", "timeout")],
            ["F-cStat"] + (["F-prot"] if c in COM_NPROT else []))  # fmt: skip
    k = chave(830)
    add("UC04-RECI-01", "uc04_cstat_539_em_consulta_recibo",
        ret("retConsReciNFe", prot(k, "539", outros[8][1])), [], ["F-cStat", "G-539"])  # fmt: skip
    # 539 com reenvio de chave regerada (revisao P7, decisao 1)
    for i in range(1, 4):
        k1, k2 = chave_cnf(980 + i, 11111111), chave_cnf(980 + i, 22222222)
        add(f"UC04-539H-{i:02d}", "uc04_539_reenvio_chave_regerada", prot(k2, "539", outros[8][1]),
            [ev("A", k1, "2026-11-11T12:00:00Z", "sent"), ev("T", k1, "2026-11-11T12:00:30Z", "timeout"),
             ev("B", k2, "2026-11-11T12:00:31Z", "sent")], ["F-cStat", "H539"])  # fmt: skip
    k1, k2 = chave_cnf(990, 11111111), chave_cnf(991, 22222222)
    add("UC04-539G-01", "uc04_539_numero_diferente", prot(k2, "539", outros[8][1]),
        [ev("A", k1, "2026-11-11T12:00:00Z", "sent"), ev("T", k1, "2026-11-11T12:00:30Z", "timeout"),
         ev("B", k2, "2026-11-11T12:00:31Z", "sent")], ["F-cStat", "G-539"])  # fmt: skip

    # UC05: lote e nfeProc
    for i in range(1, 3):
        add(f"UC05-LOTE-{i:02d}", "uc05_lote_dois_protocolos",
            ret("retConsReciNFe", prot(chave(840 + i), "204", dup), prot(chave(850 + i), "100", outros[0][1])),
            [], ["G-lote"])  # fmt: skip
    k = chave(860)
    add("UC05-LOTE-03", "uc05_lote_dois_protocolos_com_timeout",
        ret("retConsReciNFe", prot(k, "204", dup), prot(chave(861), "100", outros[0][1])),
        [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("T", k, "2026-11-11T12:00:30Z", "timeout")],
        ["G-lote", "F-timeout", "H-timeout"])  # fmt: skip
    kp = chave_do_positivo()
    for i in range(1, 3):
        add(f"UC05-PROC-{i:02d}", "uc05_nfeproc_autorizada",
            nfe_proc(POSITIVO, prot(kp, "100", outros[0][1])), [], ["F-cStat", "F-prot"])  # fmt: skip
        add(f"UC05-PROCKEY-{i:02d}", "uc05_nfeproc_protocolo_de_outra_nota",
            nfe_proc(POSITIVO, prot(chave(870 + i), "100", outros[0][1])), [], ["F-protkey", "F-cStat"])  # fmt: skip

    # UC06: assinatura (P9)
    sv = POSITIVO.index("<SignatureValue>") + len("<SignatureValue>")
    sig_casos = [
        ("UC06-SIG-01", "uc06_assinatura_valor_alterado", POSITIVO[:sv] + "AAAA" + POSITIVO[sv + 4 :], ["F-sig"]),
        ("UC06-SIG-02", "uc06_assinatura_algoritmo_fora_do_perfil",
         POSITIVO.replace("xmldsig#rsa-sha1", "xmldsig-more#rsa-md5", 1), ["F-xsd", "G-sig"]),
        ("UC06-SIG-03", "uc06_assinatura_reference_trocada",
         POSITIVO.replace(f'URI="#NFe{kp}"', 'URI="#outro"', 1), ["G-sig"]),
        ("UC06-SIG-04", "uc06_assinatura_transform_extra",
         POSITIVO.replace("</Transforms>", '<Transform Algorithm="http://www.w3.org/TR/1999/REC-xpath-19991116"/></Transforms>', 1),
         ["F-xsd", "G-sig"]),
        ("UC06-SIG-05", "uc06_comentario_fora_do_assinado",
         POSITIVO.replace("</NFe>", "<!-- anotacao do integrador --></NFe>", 1), []),
        ("UC06-SIG-06", "uc06_assinatura_cnpj_divergente", VARIANTE_CNPJ, ["F-sigcnpj"]),
        ("UC06-SIG-07", "uc06_assinatura_certificado_vencido", VARIANTE_VENCIDO, ["F-sigvalid"]),
        ("UC06-SIG-08", "uc06_protocolo_com_assinatura_sem_id",
         prot(chave(880), "100", outros[0][1]).replace(
             "</infProt>", f'</infProt><Signature xmlns="{DS}"/>'), ["F-cStat", "F-prot", "F-xsd", "G-sig"]),
        ("UC06-SIG-09", "uc06_assinatura_removida",
         POSITIVO[: POSITIVO.index("<Signature")] + POSITIVO[POSITIVO.index("</Signature>") + 12 :],
         ["F-xsd"]),
    ]  # fmt: skip
    for cid, sc, payload, exp in sig_casos:
        add(cid, sc, payload, [], exp)

    # UC02: mais timeouts sem XML
    for i in range(1, 4):  # dois timeouts, sem resposta apos o ultimo
        k = chave(900 + i)
        add(f"UC02-DUPLO-{i:02d}", "uc02_dois_timeouts_sem_retorno", None,
            [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("T1", k, "2026-11-11T12:00:30Z", "timeout"),
             ev("B", k, "2026-11-11T12:00:31Z", "sent"), ev("T2", k, "2026-11-11T12:01:01Z", "timeout")],
            ["G-noxml", "F-timeout", "H-timeout"])  # fmt: skip
    for i in range(1, 4):  # resposta ANTES do ultimo timeout nao conta
        k = chave(910 + i)
        add(f"UC02-RESPANTES-{i:02d}", "uc02_resposta_antes_do_ultimo_timeout", None,
            [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("R", k, "2026-11-11T12:00:10Z", "response"),
             ev("B", k, "2026-11-11T12:00:20Z", "sent"), ev("T", k, "2026-11-11T12:00:50Z", "timeout")],
            ["G-noxml", "F-timeout", "H-timeout"])  # fmt: skip
    k = chave(920)
    add("UC02-SEMTO-01", "uc02_envio_sem_timeout", None,
        [ev("A", k, "2026-11-11T12:00:00Z", "sent"), ev("R", k, "2026-11-11T12:00:02Z", "response")],
        ["G-noxml"])  # fmt: skip

    # UC03: ambiente
    for i in range(1, 3):
        add(f"UC03-AMBPROT-{i:02d}", "uc03_protocolo_homologacao_em_producao",
            prot(chave(930 + i), "100", outros[0][1]), [], ["F-cStat", "F-prot", "F-amb"], env="production")  # fmt: skip
        add(f"UC03-PRODOK-{i:02d}", "uc03_protocolo_producao_em_producao",
            prot(chave(940 + i), "100", outros[0][1], tp_amb="1"), [], ["F-cStat", "F-prot"], env="production")  # fmt: skip

    # Seguranca
    add(
        "SEC-BOMB-01",
        "sec_entity_expansion",
        '<!DOCTYPE b [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><protNFe>&b;</protNFe>',
        [],
        ["G-xml"],
    )
    add(
        "SEC-DTDEXT-01",
        "sec_dtd_externo",
        '<!DOCTYPE protNFe SYSTEM "http://exemplo.invalid/x.dtd"><protNFe/>',
        [],
        ["G-xml"],
    )
    add("SEC-XSI-01", "sec_schema_location_remoto_ignorado",
        prot(chave(950), "100", outros[0][1]).replace(
            "<protNFe ", '<protNFe xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xsi:schemaLocation="http://www.portalfiscal.inf.br/nfe http://exemplo.invalid/x.xsd" '), [], ["F-cStat", "F-prot"])  # fmt: skip
    add(
        "SEC-NS-01",
        "sec_namespace_nao_fiscal",
        prot(chave(951), "204", dup, ns=False),
        [],
        ["G-ns"],
    )
    add(
        "SEC-XINC-01",
        "sec_xinclude_nao_processado",
        '<x xmlns:xi="http://www.w3.org/2001/XInclude"><xi:include href="file:///etc/passwd"/></x>',
        [],
        ["G-ns"],
    )

    # Lacunas de entrada
    k = chave(960)
    add("GAP-CUF-01", "gap_cuf_invalido", prot("99" + k[2:43] + chave_dv("99" + k[2:43]), "204", dup), [],
        ["F-cStat", "F-dv"])  # fmt: skip
    add("GAP-REF-01", "gap_eventos_de_dois_documentos", None,
        [ev("A", chave(961), "2026-11-11T12:00:00Z", "sent"), ev("T", chave(962), "2026-11-11T12:00:30Z", "timeout")],
        ["G-noxml", "G-ref"])  # fmt: skip
    add(
        "GAP-EVT-01",
        "gap_evento_desconhecido",
        None,
        [ev("A", chave(963), "2026-11-11T12:00:00Z", "cancelado")],
        ["G-noxml", "G-events"],
    )
    add(
        "GAP-EVT-02",
        "gap_evento_sem_campo",
        None,
        [{"evidence_id": "A", "at": "2026-11-11T12:00:00Z", "event": "sent"}],
        ["G-noxml", "G-events"],
    )
    add(
        "GAP-EVT-03",
        "gap_horario_invalido",
        None,
        [ev("A", chave(964), "ontem a tarde", "sent")],
        ["G-noxml", "G-events"],
    )
    add("GAP-EVT-04", "gap_horario_numerico", None,
        [ev("A", chave(965), 1731326400, "sent")], ["G-noxml", "G-events"])  # fmt: skip
    add("GAP-VAZIO-01", "gap_payload_vazio", "", [], ["G-noxml"])


def chave_dv(body: str) -> str:
    return _dv(body)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows = casos()
    OUT.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    print(f"{len(rows)} casos -> {OUT}")


if __name__ == "__main__":
    main()
