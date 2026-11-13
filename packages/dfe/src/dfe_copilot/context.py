"""Contrato de contexto do produto DF-e (antes DA-93 no core; movido pela DA-94)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

MAX_EVENTS = 200

# DA-95: forma de um evento da linha do tempo, publicada no schema do contexto.
TIMELINE_EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["evidence_id", "document_ref", "at", "event"],
    "properties": {
        "evidence_id": {"type": "string", "title": "evidence_id", "maxLength": 64},
        "document_ref": {
            "type": "string",
            "title": "document_ref",
            "description": "Chave de acesso (44 digitos).",
            "pattern": "^[0-9]{44}$",
        },
        "at": {"type": "string", "format": "date-time", "title": "at"},
        "event": {
            "type": "string",
            "enum": ["sent", "timeout", "response", "error"],
            "title": "event",
        },
    },
}


class FiscalContext(BaseModel):
    """DA-93: contexto do dominio fiscal DF-e, versionado e opcional.

    `interface_type` continua significando mecanismo de transporte; o dominio
    fiscal entra aqui. Fase 1 aceita so o recorte do MVP (NF-e modelo 55,
    leiaute 4.00) - ampliar e' decisao registrada, nao edicao de Literal."""

    schema_version: Literal["1"] = "1"
    family: Literal["nfe"] = "nfe"
    model: Literal["55"] = "55"
    layout_version: Literal["4.00"] = "4.00"
    environment: Literal["homologation", "production"]
    jurisdiction: str = Field(
        pattern=r"^(AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO)$",
        description="UF de contexto do documento (sigla).",
    )
    event_time: datetime | None = Field(
        default=None, description="Instante do evento, com fuso; usado para filtrar vigencia."
    )

    # A validacao de cada evento fica em fiscal/correlation.py: evento ruim
    # vira lacuna G-events (com o motivo), nunca 422. O schema dos itens abaixo
    # e' o contrato da UI (formulario gerado), nao uma segunda validacao.
    events: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=MAX_EVENTS,
        description="Linha do tempo (sent/timeout/response/error) com document_ref = chave de acesso.",
        json_schema_extra={"items": TIMELINE_EVENT_SCHEMA},
    )
