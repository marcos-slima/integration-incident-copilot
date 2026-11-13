"""Repository para controle de versão de prompts (DA-84)."""

import hashlib
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PromptVersion

logger = logging.getLogger(__name__)


class PromptVersionRepository:
    """Repository para gerenciar versões de prompts (DA-84)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_current_version(self, prompt_id: str) -> PromptVersion | None:
        """Obtém a versão atual de um prompt."""
        stmt = select(PromptVersion).where(
            PromptVersion.prompt_id == prompt_id,
            PromptVersion.is_current.is_(True),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_version(self, prompt_id: str, version: int) -> PromptVersion | None:
        """Obtém uma versão específica de um prompt."""
        stmt = select(PromptVersion).where(
            PromptVersion.prompt_id == prompt_id,
            PromptVersion.version == version,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_versions(self, prompt_id: str) -> list[PromptVersion]:
        """Lista todas as versões de um prompt, ordenadas por número."""
        stmt = (
            select(PromptVersion)
            .where(PromptVersion.prompt_id == prompt_id)
            .order_by(PromptVersion.version.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def add_version(
        self, prompt_id: str, content: str, version: int, is_current: bool = False
    ) -> PromptVersion:
        """Adiciona uma nova versão de um prompt."""
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # SQLite + SQLAlchemy async + StaticPool tem flush tardio. Se a versão
        # atual está marcada como is_current=True no ORM memory mas não foi
        # flushada para o DB, o próximo INSERT com is_current=True causará
        # violação. Solução: validar antes de escrever e atualizar o DB.
        if is_current:
            current = await self.get_current_version(prompt_id)
            if current is not None:
                current.is_current = False
                await self._session.flush()  # force UPDATE para DB

        version_entry = PromptVersion(
            prompt_id=prompt_id,
            version=version,
            digest=digest,
            content=content,
            is_current=is_current,
            created_at=datetime.now(UTC),
        )
        self._session.add(version_entry)
        await self._session.flush()

        return version_entry

    async def rollback_to_version(
        self, prompt_id: str, target_version: int
    ) -> PromptVersion | None:
        """Faz rollback para uma versão específica."""
        target = await self.get_version(prompt_id, target_version)
        if target is None:
            return None

        # Desmarcar a versão atual atual ( para SQLite compatibility)
        # Mesmo problema do add_version: flush tardio faz olhar antigo de
        # is_current no DB durante o test rollback.
        current = await self.get_current_version(prompt_id)
        if current is not None and current.version != target_version:
            current.is_current = False
            await self._session.flush()  # force UPDATE antes do INSERT

        # Marcar a versão alvo como atual
        target.is_current = True
        await self._session.flush()

        return target
