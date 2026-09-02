"""Modelos Pydantic do SAP Integration Copilot."""

from typing import Literal

from pydantic import BaseModel, Field

# Limites generosos - rejeitam entrada absurda (ex: 1MB de log colado
# por engano) com 422 claro, sem impedir uso legitimo de logs longos.
MAX_DESCRIPTION_LENGTH = 5_000
MAX_LOGS_LENGTH = 50_000
MAX_PAYLOAD_LENGTH = 50_000


class IncidentRequest(BaseModel):
    description: str = Field(max_length=MAX_DESCRIPTION_LENGTH)
    logs: str | None = Field(default=None, max_length=MAX_LOGS_LENGTH)
    payload: str | None = Field(default=None, max_length=MAX_PAYLOAD_LENGTH)
    interface_type: Literal["odata", "rfc", "servicenow"] | None = None
    identifier: str | None = None  # ex: nome do iFlow, RFC destination, numero de IDoc/incidente


class DiagnosisResponse(BaseModel):
    probable_root_cause: str
    confidence: float = Field(ge=0.0, le=1.0)
    next_steps: list[str]
    report_markdown: str
    matched_source: str | None = None
