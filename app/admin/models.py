"""DA-46/48 — modelos ORM do registro de modelos e metering.

Tres tabelas, todas opt-in (so importam sem efeito quando DATABASE_URL
esta configurada — mesmo padrao de app/services/incident_repository.py):

    llm_models        catalogo de modelos por ORIGEM (DA-45: a origem
                      real, e nao o rotulo do provider, e a chave de
                      identidade; o nome do modelo NUNCA entra em tabela
                      de rotas — invariante #8).
    llm_credentials   credencial por origem, CIFRADA com Fernet (DA-47).
    llm_usage         acumulador de tokens reais por (origem, modelo)
                      num periodo aberto (DA-48); percentual consumido
                      e calculado contra llm_models.monthly_limit_tokens.
    integration_systems  catalogo de SISTEMAS integrados (DA-49): o
                      endpoint/vendor/conector real (SAP, Servicenow,
                      Salesforce, Workday, Ariba, CAP, APIM) que aparece
                      como connector_source_system/interface_type nos
                      incidentes. Fase B da superficie admin.

Colunas foram escolhidas compativeis com PostgreSQL E SQLite de teste
(dialeto async aiosqlite): `Uuid` nativo-driver, sem JSONB, sem tipos
exclusivos de PG — assim os testes unitarios reutilizam os mesmos
modelos de producao em vez de manter um espelho (diferente da abordagem
de test_incident_repository.py, forcada por JSONB/UUID PG do Incident).

Faremos `CREATE TABLE` manualmente por tabela nos testes; o metadata
completo da app e registrado no Alembic (alembic/env.py) para que a
migracao gerada com --autogenerate parta destas definicoes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# ---------------------------------------------------------------------------
# llm_models
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


class LlmModel(Base):
    """Modelo de LLM registrado para uso ao runtime (DA-46).

    provider_origin segue o mesmo identificador normalizado da DA-45
    (app/llm/origins.py): "api.openai.com", "api.groq.com", "local_lab",
    etc. — a origem EXISTE (o dado sai para onde a rota declara aceitar),
    independente do rotulo do provider.
    """

    __tablename__ = "llm_models"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_origin: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Precos em USD por 1 milhao de tokens (None = tabela interna do
    # gateway e usada na captura; administrativo nao bloqueia nada).
    price_in_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_out_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Limite mensal de tokens (None = sem limite; percentual fica null).
    monthly_limit_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        UniqueConstraint("provider_origin", "model_id", name="uq_llm_models_origin_model"),
    )

    def __repr__(self) -> str:
        return (
            f"<LlmModel origin={self.provider_origin} model={self.model_id} enabled={self.enabled}>"
        )


# ---------------------------------------------------------------------------
# llm_credentials
# ---------------------------------------------------------------------------


class LlmCredential(Base):
    """Credencial de um provider por ORIGEM, cifrada (DA-47).

    Uma linha por provider_origin (chave primaria). `encrypted_key` e um
    token Fernet; `masked` guarda a representacao segura para a UI
    (prefixo 4 + '****' + sufixo 4) — a API nunca devolve plaintext.
    """

    __tablename__ = "llm_credentials"

    provider_origin: Mapped[str] = mapped_column(String(128), primary_key=True)
    encrypted_key: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    masked: Mapped[str] = mapped_column(String(16), nullable=False)
    last_test_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    def __repr__(self) -> str:
        return f"<LlmCredential origin={self.provider_origin} v{self.key_version} masked={self.masked}>"


# ---------------------------------------------------------------------------
# llm_usage
# ---------------------------------------------------------------------------


class LlmUsage(Base):
    """Acumulador de tokens reais por (origem, modelo) num periodo.

    Um periodo aberto por tupla (period_end IS NULL): o metering
    (DA-48) incrementa as colunas; `POST /admin/usage/{origin}/{model}/reset`
    fecha o periodo atual (period_end=agora) e abre outro, permitindo
    percentuais mensais e historico.

    `cost_usd` e a soma do custo calculado na captura (precos do gateway
    ou do registro com prioridade quando disponivel).
    """

    __tablename__ = "llm_usage"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_origin: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tokens_in: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        # Unico periodo aberto por (origem, modelo). Indice parcial: em
        # PostgreSQL NULLs sao distintos num UNIQUE simples, entao a
        # unicidade so faz sentido filtrando period_end IS NULL.
        Index(
            "uq_llm_usage_open",
            "provider_origin",
            "model_id",
            unique=True,
            sqlite_where=text("period_end IS NULL"),
            postgresql_where=text("period_end IS NULL"),
        ),
        Index("ix_llm_usage_origin_created", "provider_origin", "period_start"),
    )

    def __repr__(self) -> str:
        return (
            f"<LlmUsage origin={self.provider_origin} model={self.model_id} "
            f"in={self.tokens_in} out={self.tokens_out}>"
        )


# ---------------------------------------------------------------------------
# integration_systems (DA-49, Fase B)
# ---------------------------------------------------------------------------

# Literal fechado dos conectores — o MESMO conjunto de interface_type do
# pipeline (app/models.py::IncidentRequest) para o sistema poder ser
# relacionado ao incidente via connector_type.
CONNECTOR_TYPES = ("odata", "rfc", "servicenow", "salesforce", "workday", "ariba", "cap", "apim")

SYSTEM_STATUSES = ("active", "degraded", "offline", "trial")
SYSTEM_ENVIRONMENTS = ("prod", "stage", "dev", "test")


class IntegrationSystem(Base):
    """Sistema integrado gerenciado (DA-49, Fase B da superficie admin).

    E o CATALOGO operacional dos sistemas que aparecem nos incidentes
    (connector_source_system / interface_type): o registro permite ao
    operador manter o mapa SAP/Servicenow/Salesforce/Workday/Ariba/CAP/
    APIM do ambiente, com status e ambiente de deploy — sem editar .env.

    `system_key` e o slug unico (ex: "sap_odata_prod") usado para
    referenciar o sistema de forma estavel. `connector_type` usa o
    Literal fechado do pipeline (odata/rfc/servicenow/salesforce/workday/
    ariba/successfactors/cap/apim) — a ponte natural para correlacionar
    incidentes. O gate `connector_reachable` (DA-51) amarra esta lista ao
    registro real de `app/connectors/__init__.py`, para o docstring nao
    deriva do Literal sem ninguem perceber.
    """

    __tablename__ = "integration_systems"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    system_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    vendor: Mapped[str] = mapped_column(String(64), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    base_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    environment: Mapped[str] = mapped_column(String(16), default="prod", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (Index("ix_integration_systems_vendor_key", "vendor", "system_key"),)

    def __repr__(self) -> str:
        return (
            f"<IntegrationSystem key={self.system_key} vendor={self.vendor} "
            f"type={self.connector_type} status={self.status}>"
        )


def percent_consumed(
    *, tokens_in: int, tokens_out: int, monthly_limit_tokens: int | None
) -> int | None:
    """Percentual consumido do limite mensal (None se sem limite)."""
    if not monthly_limit_tokens or monthly_limit_tokens <= 0:
        return None
    total = (tokens_in or 0) + (tokens_out or 0)
    return int(round(total * 100.0 / monthly_limit_tokens, ndigits=0))
