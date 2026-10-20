"""Modelos ORM para DA-85 (A/B Testing)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ABExperiment(Base):
    """Modelo ORM para experimentos A/B testing (DA-85).

    Schema:
        experiment_id: identificador único do experimento
        name: nome do experimento
        description: descrição do experimento
        variant_a_prompt: ID ou hash do prompt variante A
        variant_b_prompt: ID ou hash do prompt variante B
        starting_at: data/hora de início do experimento
        ending_at: data/hora de término do experimento
        is_active: indica se o experimento está ativo

    Migration: 025_add_ab_experiments_table
    """

    __tablename__ = "ab_experiments"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    experiment_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    variant_a_prompt: Mapped[str] = mapped_column(String(64), nullable=False)
    variant_b_prompt: Mapped[str] = mapped_column(String(64), nullable=False)
    starting_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ending_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<ABExperiment {self.experiment_id} name={self.name} active={self.is_active}>"
