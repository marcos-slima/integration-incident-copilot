"""Repository para A/B testing (DA-85)."""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ABExperiment

logger = logging.getLogger(__name__)


class ABExperimentRepository:
    """Repository para gerenciar experimentos A/B (DA-85)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, experiment_id: str) -> ABExperiment | None:
        """Obtém um experimento pelo ID."""
        stmt = select(ABExperiment).where(ABExperiment.experiment_id == experiment_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active(self, now: datetime | None = None) -> list[ABExperiment]:
        """Experimentos em execução em `now` (default: agora, UTC).

        Ativo = `is_active` ligado e `now` dentro da janela
        [starting_at, ending_at): sem `starting_at` já começou; sem
        `ending_at` não termina. Antes o filtro era `starting_at IS NULL`,
        o que excluía todo experimento com data de início, inclusive os já
        iniciados.
        """
        now = now or datetime.now(UTC)
        stmt = (
            select(ABExperiment)
            .where(
                ABExperiment.is_active.is_(True),
                or_(ABExperiment.starting_at.is_(None), ABExperiment.starting_at <= now),
                or_(ABExperiment.ending_at.is_(None), ABExperiment.ending_at > now),
            )
            .order_by(ABExperiment.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def list_experiments(self, include_inactive: bool = False) -> list[ABExperiment]:
        """Lista experimentos pelo flag `is_active` (sem olhar a janela de
        datas - para "em execução agora" use `get_active`)."""
        stmt = select(ABExperiment).order_by(ABExperiment.created_at.desc())
        if not include_inactive:
            stmt = stmt.where(ABExperiment.is_active.is_(True))
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def create_experiment(
        self,
        name: str,
        description: str,
        variant_a_prompt: str,
        variant_b_prompt: str,
        starting_at: datetime | None = None,
        ending_at: datetime | None = None,
        is_active: bool = False,
    ) -> ABExperiment:
        """Cria um novo experimento A/B."""
        experiment = ABExperiment(
            experiment_id=str(uuid.uuid4()),
            name=name,
            description=description,
            variant_a_prompt=variant_a_prompt,
            variant_b_prompt=variant_b_prompt,
            starting_at=starting_at,
            ending_at=ending_at,
            is_active=is_active,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self._session.add(experiment)
        await self._session.flush()
        return experiment

    async def update_experiment(self, experiment_id: str, fields: dict) -> ABExperiment | None:
        """Atualiza um experimento existente."""
        experiment = await self.get_by_id(experiment_id)
        if experiment is None:
            return None

        allowed_fields = {
            "name",
            "description",
            "variant_a_prompt",
            "variant_b_prompt",
            "starting_at",
            "ending_at",
            "is_active",
        }

        for key, value in fields.items():
            if key in allowed_fields:
                setattr(experiment, key, value)

        experiment.updated_at = datetime.now(UTC)
        await self._session.flush()
        return experiment

    async def delete_experiment(self, experiment_id: str) -> bool:
        """Remove um experimento."""
        experiment = await self.get_by_id(experiment_id)
        if experiment is None:
            return False

        await self._session.delete(experiment)
        await self._session.flush()
        return True
