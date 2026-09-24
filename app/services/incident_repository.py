"""Repositório de incidentes — persistência PostgreSQL (Fase 1 Observabilidade).

Modelo de dados:
    incidents   → cada chamada a /diagnose gera um registro
    verifications → cada chamada a /incidents/{id}/verify atualiza o mesmo

Padrão de acesso:
    repo = IncidentRepository(session)
    incident = await repo.create(body, response)
    await repo.update_verification(incident_id, verify_req)

A tabela `incidents` é a fonte de verdade para os dashboards Grafana
(Fase 3) e relatórios agendados (Fase 4). Cada coluna mapeia diretamente
a uma métrica ou filtro relevante para COI/IOC, SOC e iPaaS:

    COI/IOC  : volume diário, SLA, agent_domain, interface_type
    SOC      : sensitivity_level, pii_detected, redaction_applied
    iPaaS    : connector_source_system, error_codes, llm_provider_used
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# ---------------------------------------------------------------------------
# Modelo ORM
# ---------------------------------------------------------------------------


class Incident(Base):
    """Tabela de incidentes diagnosticados.

    Compatível com TimescaleDB: basta executar
        SELECT create_hypertable('incidents', 'created_at');
    após o `alembic upgrade head` para transformar em hypertable.
    """

    __tablename__ = "incidents"

    # Chave primária
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Rastreabilidade — liga ao trace Langfuse
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    # Entrada do diagnóstico
    interface_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    connector_source_system: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Resultado do diagnóstico
    probable_root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    diagnosis_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_strength: Mapped[str | None] = mapped_column(String(32), nullable=True)
    llm_provider_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_domain: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Evidências estruturadas (JSON — permite queries analíticas no Grafana)
    evidence_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Segurança / SOC
    sensitivity_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pii_detected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    redaction_applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Métricas de execução
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_codes: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )

    # Verificação humana (preenchida por /incidents/{id}/verify)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    diagnosis_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verified_root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Índices compostos para as queries típicas dos dashboards
    __table_args__ = (
        Index("ix_incidents_agent_created", "agent_domain", "created_at"),
        Index("ix_incidents_provider_created", "llm_provider_used", "created_at"),
        Index("ix_incidents_sensitivity", "sensitivity_level", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Incident id={self.id} agent={self.agent_domain} created={self.created_at}>"


# ---------------------------------------------------------------------------
# Repositório
# ---------------------------------------------------------------------------


class IncidentRepository:
    """CRUD assíncrono para a tabela `incidents`.

    Instanciado por request (não singleton) para aproveitar a sessão
    da dependency FastAPI `get_db_session`.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Criação
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        # campos da request original
        interface_type: str | None = None,
        description: str | None = None,
        connector_source_system: str | None = None,
        is_mock: bool = False,
        sensitivity_level: str | None = None,
        pii_detected: bool = False,
        redaction_applied: bool = False,
        latency_ms: int | None = None,
        error_codes: list[str] | None = None,
        # campos da DiagnosisResponse
        trace_id: str | None = None,
        probable_root_cause: str | None = None,
        model_confidence: float | None = None,
        diagnosis_confidence: float | None = None,
        evidence_strength: str | None = None,
        llm_provider_used: str | None = None,
        agent_domain: str | None = None,
        evidence_json: Any | None = None,
    ) -> Incident:
        """Persiste um novo diagnóstico e retorna o registro criado."""
        incident = Incident(
            interface_type=interface_type,
            description=description,
            connector_source_system=connector_source_system,
            is_mock=is_mock,
            sensitivity_level=sensitivity_level,
            pii_detected=pii_detected,
            redaction_applied=redaction_applied,
            latency_ms=latency_ms,
            error_codes=error_codes,
            trace_id=trace_id,
            probable_root_cause=probable_root_cause,
            model_confidence=model_confidence,
            diagnosis_confidence=diagnosis_confidence,
            evidence_strength=evidence_strength,
            llm_provider_used=llm_provider_used,
            agent_domain=agent_domain,
            evidence_json=evidence_json,
        )
        self._session.add(incident)
        await self._session.flush()  # obter id sem commit (commit pelo get_db_session)
        return incident

    # ------------------------------------------------------------------
    # Verificação humana
    # ------------------------------------------------------------------

    async def update_verification(
        self,
        incident_id: uuid.UUID | str,
        *,
        diagnosis_correct: bool,
        verified_by: str | None = None,
        verified_root_cause: str | None = None,
    ) -> Incident | None:
        """Registra o resultado da verificação humana.

        Retorna o Incident atualizado, ou None se não encontrado.
        """
        if isinstance(incident_id, str):
            try:
                incident_id = uuid.UUID(incident_id)
            except ValueError:
                return None

        result = await self._session.execute(select(Incident).where(Incident.id == incident_id))
        incident = result.scalar_one_or_none()
        if incident is None:
            return None

        incident.verified_at = datetime.now(UTC)
        incident.diagnosis_correct = diagnosis_correct
        incident.verified_by = verified_by
        incident.verified_root_cause = verified_root_cause
        await self._session.flush()
        return incident

    # ------------------------------------------------------------------
    # Consultas analíticas (usadas pelos relatórios — Fase 4)
    # ------------------------------------------------------------------

    async def get_by_id(self, incident_id: uuid.UUID | str) -> Incident | None:
        if isinstance(incident_id, str):
            try:
                incident_id = uuid.UUID(incident_id)
            except ValueError:
                return None
        result = await self._session.execute(select(Incident).where(Incident.id == incident_id))
        return result.scalar_one_or_none()

    async def list_recent(self, limit: int = 50) -> list[Incident]:
        result = await self._session.execute(
            select(Incident).order_by(Incident.created_at.desc()).limit(limit)
        )
        return list(result.scalars())
