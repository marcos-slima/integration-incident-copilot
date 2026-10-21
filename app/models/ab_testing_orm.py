"""Modelos ORM para DA-85 (A/B Testing)."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ABExperiment(Base):
    """Modelo ORM para experimentos A/B testing (DA-85).

    Schema alinhado a migration 025 (fonte de verdade do banco):
        experiment_id: identificador único do experimento (36 chars)
        name: nome do experimento
        description: descrição do experimento
        variant_a_prompt: ID ou hash do prompt variante A
        variant_b_prompt: ID ou hash do prompt variante B
        starting_at: data/hora de início do experimento
        ending_at: data/hora de término do experimento
        is_active: indica se o experimento está ativo (default False, igual ao
            repositório: experimento nasce desligado)

    Migration: 025_add_ab_experiments_table
    """

    __tablename__ = "ab_experiments"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    experiment_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    variant_a_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    variant_b_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    starting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ending_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        UniqueConstraint("experiment_id", name="uq_ab_experiments_id"),
        Index("ix_ab_experiments_experiment_id", "experiment_id"),
    )

    def __repr__(self) -> str:
        return f"<ABExperiment {self.experiment_id} name={self.name} active={self.is_active}>"
