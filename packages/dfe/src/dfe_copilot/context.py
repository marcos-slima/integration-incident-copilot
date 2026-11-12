"""Contrato de contexto do produto DF-e (antes DA-93 no core; movido pela DA-94)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

MAX_EVENTS = 200


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

    events: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=MAX_EVENTS,
        description="Linha do tempo (sent/timeout/response/error) com document_ref = chave de acesso.",
    )
