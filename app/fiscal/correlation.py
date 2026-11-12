"""DA-93 (fase 3): linha do tempo e regras fiscais deterministicas.

Correlacao so por identificador explicito (`document_ref` = chave de acesso):
nunca por similaridade de texto nem por proximidade temporal sozinha.
As regras produzem `findings` tipados; hipotese nunca vira causa confirmada
aqui - isso so acontece por revisao humana (estudo §6.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

EventKind = Literal["sent", "timeout", "response", "error"]
_KINDS: frozenset[str] = frozenset({"sent", "timeout", "response", "error"})


class FiscalCorrelationError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, order=True)
class TimelineEvent:
    at: datetime  # sempre UTC
    evidence_id: str
    document_ref: str
    event: EventKind
    request_id: str | None = None


def normalize_events(raw: list[dict[str, Any]]) -> list[TimelineEvent]:
    """Valida e ordena eventos; horario sem fuso e' recusado (ambiguo)."""
    out: list[TimelineEvent] = []
    for item in raw:
        kind = item.get("event")
        if kind not in _KINDS:
            raise FiscalCorrelationError("unknown_event", str(kind))
        for key in ("evidence_id", "document_ref", "at"):
            if not item.get(key):
                raise FiscalCorrelationError("missing_field", key)
        at = datetime.fromisoformat(str(item["at"]))  # 3.11+ aceita "Z"
        if at.tzinfo is None:
            raise FiscalCorrelationError("naive_timestamp", item["evidence_id"])
        out.append(
            TimelineEvent(
                at=at.astimezone(UTC),
                evidence_id=str(item["evidence_id"]),
                document_ref=str(item["document_ref"]),
                event=kind,  # type: ignore[arg-type]
                request_id=item.get("request_id"),
            )
        )
    return sorted(out)


def _base() -> dict[str, Any]:
    return {
        "findings": [],
        "cause_status": "inconclusive",
        "resolution_status": "not_validated",
        "remote_authorization_confirmed": False,
        "automatic_actions": [],
    }


def diagnose_duplicity(xml_facts: dict[str, Any], events: list[TimelineEvent]) -> dict[str, Any]:
    """Caso cStat 204 (UC01): H1 = reenvio apos timeout.

    H1 exige, para o MESMO documento (chave do retorno == document_ref):
    >= 2 envios e um timeout entre o primeiro e o ultimo envio.
    """
    out = _base()
    key = xml_facts.get("access_key")
    if (
        xml_facts.get("document_cStat") != "204"
        or not key
        or not xml_facts.get("access_key_dv_valid")
    ):
        out["next_step"] = "Obter retorno do documento com chave valida e cStat aplicavel."
        return out
    out["findings"].append({
        "id": "F-204", "kind": "fact", "source": "rule_reference",
        "statement": "cStat 204 (duplicidade) no retorno do documento.",
        "rule_ref": "NT2018.005_v1.51_sec4.5_2B08-20",
        "evidence_refs": [f"sha256:{xml_facts.get('raw_sha256')}"],
    })  # fmt: skip
    doc = [e for e in events if e.document_ref == key]
    sent = [e for e in doc if e.event == "sent"]
    timeouts = [e for e in doc if e.event == "timeout"]
    if len(sent) >= 2 and any(sent[0].at < t.at < sent[-1].at for t in timeouts):
        out["cause_status"] = "hypothesis"
        out["findings"].append({
            "id": "H1", "kind": "hypothesis", "source": "tool",
            "statement": "Reenvio apos timeout pode explicar a duplicidade.",
            "evidence_refs": [e.evidence_id for e in doc],
            "confirmation_test": [
                "Consulta autorizada de situacao/protocolo no ambiente correto",
                "Vinculo de IDs entre tentativas verificado",
                "Confirmacao humana do mecanismo de reenvio",
            ],
            "alternatives": ["retransmissao automatica", "envio manual concorrente",
                             "divergencia de estado mensageria x backend"],
        })  # fmt: skip
    else:
        out["findings"].append({
            "id": "G-204", "kind": "fact", "source": "tool",
            "statement": "Sem evidencia de reenvio apos timeout para este documento.",
            "evidence_refs": [e.evidence_id for e in doc],
        })  # fmt: skip
    out["next_step"] = (
        "Conciliar situacao/protocolo por leitura autorizada; nao reenviar cegamente."
    )
    return out


def diagnose_timeout(document_ref: str, events: list[TimelineEvent]) -> dict[str, Any]:
    """UC02: timeout sem retorno posterior => resultado do processamento desconhecido.

    Nunca afirma indisponibilidade do autorizador: um timeout local nao prova isso.
    """
    out = _base()
    doc = [e for e in events if e.document_ref == document_ref]
    timeouts = [e for e in doc if e.event == "timeout"]
    if not timeouts:
        out["processing_outcome"] = "not_applicable"
        out["next_step"] = "Nenhum timeout registrado para o documento."
        return out
    last_timeout = timeouts[-1]
    answered = any(e.event == "response" and e.at > last_timeout.at for e in doc)
    out["processing_outcome"] = "response_received" if answered else "unknown"
    out["findings"].append({
        "id": "F-timeout", "kind": "fact", "source": "tool",
        "statement": "Timeout registrado"
        + ("; houve resposta posterior." if answered else "; sem resposta posterior."),
        "evidence_refs": [e.evidence_id for e in doc],
    })  # fmt: skip
    if not answered:
        out["cause_status"] = "hypothesis"
        out["findings"].append({
            "id": "H-timeout", "kind": "hypothesis", "source": "tool",
            "statement": "Resposta perdida ou nao processada; o documento pode ja ter sido autorizado.",
            "evidence_refs": [last_timeout.evidence_id],
            "alternatives": ["timeout local", "rede/proxy", "handshake TLS",
                             "lentidao remota", "fila assincrona parada"],
            "confirmation_test": ["Consulta autorizada de situacao/protocolo antes de qualquer reenvio"],
        })  # fmt: skip
    out["next_step"] = (
        "Consultar situacao por leitura autorizada; contingencia e reenvio sao decisao humana."
    )
    return out
