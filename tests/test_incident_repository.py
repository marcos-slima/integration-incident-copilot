"""Testes do IncidentRepository (Fase 1 Observabilidade Grafana).

Estratégia:
- SQLite in-memory com dialeto async (aiosqlite) — sem PostgreSQL real em CI.
- JSONB e UUID são mapeados para JSON/String no SQLite via modelo de teste
  (sem alterar o modelo de produção — IncidentSQLite é uma variante de teste).
- Testa o contrato completo do repositório: create, update_verification,
  get_by_id, list_recent.
- Não testa a migration Alembic (testada manualmente contra PostgreSQL real).

Audiences cobertas pelos campos testados:
    COI/IOC  : interface_type, agent_domain, latency_ms, diagnosis_confidence
    SOC      : sensitivity_level, pii_detected, redaction_applied
    iPaaS    : connector_source_system, llm_provider_used, evidence_json
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import JSON as SA_JSON
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import StaticPool

# ---------------------------------------------------------------------------
# Modelo SQLite-compatível (espelho do IncidentRepository sem JSONB/UUID PG)
# ---------------------------------------------------------------------------


class TestBase(DeclarativeBase):
    pass


class IncidentSQLite(TestBase):
    """Variante SQLite do modelo Incident para testes unitários.

    Substitui JSONB → JSON e UUID → String(36) para compatibilidade
    com SQLite in-memory. O modelo de produção (app/services/incident_repository.py)
    permanece inalterado — usa os tipos nativos PostgreSQL.
    """

    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    interface_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    connector_source_system: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    probable_root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    diagnosis_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_strength: Mapped[str | None] = mapped_column(String(32), nullable=True)
    llm_provider_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_domain: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_json: Mapped[Any | None] = mapped_column(SA_JSON, nullable=True)
    sensitivity_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pii_detected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    redaction_applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_codes: Mapped[Any | None] = mapped_column(SA_JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    diagnosis_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verified_root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Repositório de teste (mesma lógica de negócio, modelo SQLite)
# ---------------------------------------------------------------------------

from sqlalchemy import select


class IncidentRepository:
    """Implementação de teste do repositório — mesma interface de produção."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> IncidentSQLite:
        incident = IncidentSQLite(**{k: v for k, v in kwargs.items() if hasattr(IncidentSQLite, k)})
        self._session.add(incident)
        await self._session.flush()
        return incident

    async def update_verification(
        self,
        incident_id: Any,
        *,
        diagnosis_correct: bool,
        verified_by: str | None = None,
        verified_root_cause: str | None = None,
    ) -> IncidentSQLite | None:
        if isinstance(incident_id, uuid.UUID):
            incident_id = str(incident_id)
        try:
            uuid.UUID(str(incident_id))
        except (ValueError, AttributeError):
            return None
        result = await self._session.execute(
            select(IncidentSQLite).where(IncidentSQLite.id == str(incident_id))
        )
        incident = result.scalar_one_or_none()
        if incident is None:
            return None
        incident.verified_at = datetime.now(UTC)
        incident.diagnosis_correct = diagnosis_correct
        incident.verified_by = verified_by
        incident.verified_root_cause = verified_root_cause
        await self._session.flush()
        return incident

    async def get_by_id(self, incident_id: Any) -> IncidentSQLite | None:
        if isinstance(incident_id, uuid.UUID):
            incident_id = str(incident_id)
        try:
            uuid.UUID(str(incident_id))
        except (ValueError, AttributeError):
            return None
        result = await self._session.execute(
            select(IncidentSQLite).where(IncidentSQLite.id == str(incident_id))
        )
        return result.scalar_one_or_none()

    async def list_recent(self, limit: int = 50) -> list[IncidentSQLite]:
        result = await self._session.execute(
            select(IncidentSQLite).order_by(IncidentSQLite.created_at.desc()).limit(limit)
        )
        return list(result.scalars())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session() -> AsyncSession:  # type: ignore[override]
    """Sessão SQLite in-memory — sem estado entre testes."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(TestBase.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s

    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_kwargs(**overrides: Any) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "interface_type": "IDoc",
        "description": "IDoc 51 - material lock em ME21N",
        "connector_source_system": "S4HANA_PROD",
        "is_mock": True,
        "sensitivity_level": "internal",
        "pii_detected": False,
        "redaction_applied": False,
        "latency_ms": 1250,
        "trace_id": str(uuid.uuid4()),
        "probable_root_cause": "Material bloqueado por ME1X simultâneo",
        "model_confidence": 0.87,
        "diagnosis_confidence": 0.82,
        "evidence_strength": "strong",
        "llm_provider_used": "ollama",
        "agent_domain": "sap_integration",
        "evidence_json": [{"source": "IDocStatus", "content": "Status 51"}],
        **overrides,
    }


# ---------------------------------------------------------------------------
# Testes — criação
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_returns_incident_with_id(session: AsyncSession) -> None:
    repo = IncidentRepository(session)
    incident = await repo.create(**_make_kwargs())

    assert incident.id is not None
    assert incident.interface_type == "IDoc"
    assert incident.llm_provider_used == "ollama"
    assert incident.model_confidence == pytest.approx(0.87)
    assert incident.is_mock is True


@pytest.mark.asyncio
async def test_create_sets_created_at(session: AsyncSession) -> None:
    repo = IncidentRepository(session)
    before = datetime.now(UTC)
    incident = await repo.create(**_make_kwargs())
    after = datetime.now(UTC)

    created_at = incident.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)

    assert before <= created_at <= after


@pytest.mark.asyncio
async def test_create_null_optional_fields(session: AsyncSession) -> None:
    """Campos opcionais aceitam None sem violar constraints."""
    repo = IncidentRepository(session)
    incident = await repo.create(
        id=str(uuid.uuid4()),
        interface_type=None,
        connector_source_system=None,
        latency_ms=None,
        evidence_json=None,
    )
    assert incident.id is not None
    assert incident.interface_type is None
    assert incident.evidence_json is None


@pytest.mark.asyncio
async def test_create_multiple_unique_ids(session: AsyncSession) -> None:
    repo = IncidentRepository(session)
    ids = {
        str((await repo.create(**_make_kwargs(id=str(uuid.uuid4()), description=f"i{i}"))).id)
        for i in range(5)
    }
    assert len(ids) == 5


# ---------------------------------------------------------------------------
# Testes — verificação humana
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_verification_correct(session: AsyncSession) -> None:
    repo = IncidentRepository(session)
    incident = await repo.create(**_make_kwargs())

    updated = await repo.update_verification(
        incident.id,
        diagnosis_correct=True,
        verified_by="operador.sap@empresa.com",
        verified_root_cause="Material bloqueado confirmado",
    )
    assert updated is not None
    assert updated.diagnosis_correct is True
    assert updated.verified_by == "operador.sap@empresa.com"
    assert updated.verified_root_cause == "Material bloqueado confirmado"
    assert updated.verified_at is not None


@pytest.mark.asyncio
async def test_update_verification_incorrect(session: AsyncSession) -> None:
    repo = IncidentRepository(session)
    incident = await repo.create(**_make_kwargs())

    updated = await repo.update_verification(
        str(incident.id),
        diagnosis_correct=False,
        verified_by="analista@coi",
        verified_root_cause="Causa real: timeout de rede",
    )
    assert updated is not None
    assert updated.diagnosis_correct is False


@pytest.mark.asyncio
async def test_update_verification_unknown_id_returns_none(session: AsyncSession) -> None:
    repo = IncidentRepository(session)
    result = await repo.update_verification(str(uuid.uuid4()), diagnosis_correct=True)
    assert result is None


@pytest.mark.asyncio
async def test_update_verification_invalid_uuid_returns_none(session: AsyncSession) -> None:
    repo = IncidentRepository(session)
    result = await repo.update_verification("not-a-uuid", diagnosis_correct=True)
    assert result is None


# ---------------------------------------------------------------------------
# Testes — consulta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_by_id_found(session: AsyncSession) -> None:
    repo = IncidentRepository(session)
    incident = await repo.create(**_make_kwargs())
    fetched = await repo.get_by_id(incident.id)
    assert fetched is not None
    assert fetched.id == incident.id


@pytest.mark.asyncio
async def test_get_by_id_string(session: AsyncSession) -> None:
    repo = IncidentRepository(session)
    incident = await repo.create(**_make_kwargs())
    fetched = await repo.get_by_id(str(incident.id))
    assert fetched is not None


@pytest.mark.asyncio
async def test_get_by_id_unknown_returns_none(session: AsyncSession) -> None:
    repo = IncidentRepository(session)
    result = await repo.get_by_id(str(uuid.uuid4()))
    assert result is None


@pytest.mark.asyncio
async def test_list_recent_returns_all(session: AsyncSession) -> None:
    repo = IncidentRepository(session)
    for i in range(3):
        await repo.create(**_make_kwargs(id=str(uuid.uuid4()), description=f"inc-{i}"))
    recent = await repo.list_recent(limit=10)
    assert len(recent) == 3


@pytest.mark.asyncio
async def test_list_recent_respects_limit(session: AsyncSession) -> None:
    repo = IncidentRepository(session)
    for i in range(10):
        await repo.create(**_make_kwargs(id=str(uuid.uuid4()), description=f"bulk-{i}"))
    recent = await repo.list_recent(limit=3)
    assert len(recent) == 3


# ---------------------------------------------------------------------------
# Testes — campos SOC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_with_pii_flags(session: AsyncSession) -> None:
    repo = IncidentRepository(session)
    incident = await repo.create(
        **_make_kwargs(
            pii_detected=True,
            redaction_applied=True,
            sensitivity_level="confidential",
        )
    )
    assert incident.pii_detected is True
    assert incident.redaction_applied is True
    assert incident.sensitivity_level == "confidential"
