"""Modelos ORM para DA-84 (Prompt Versioning)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PromptVersion(Base):
    """Modelo ORM para controle de versão de prompts (DA-84).

    Cada prompt tem um ID lógico (prompt_id), com múltiplas versões.
    A versão atual é identificada por is_current=True.

    Schema:
        prompt_id: identificador lógico do prompt (ex: 'diagnostic_prompt')
        version: número da versão (1, 2, 3, ...)
        digest: SHA-256 do conteúdo do prompt (para detecção de mudanças)
        content: conteúdo completo do prompt
        created_at: data/hora de criação da versão
        is_current: indica se esta é a versão atualmente em uso

    Migration: 024_add_prompt_versions_table
    """

    __tablename__ = "prompt_versions"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    prompt_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("prompt_id", "version", name="uq_prompt_versions_prompt_version"),
        Index(
            "uq_prompt_versions_current",
            "prompt_id",
            "is_current",
            unique=True,
            sqlite_where=text("is_current = 1"),
            postgresql_where=text("is_current = true"),
        ),
    )

    def __repr__(self) -> str:
        return f"<PromptVersion {self.prompt_id} v{self.version} current={self.is_current}>"
