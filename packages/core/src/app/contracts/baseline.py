"""DA-52: baseline de contrato por sistema integrado.

`system_contracts` guarda o CONTRATO observado, nao um "foi alterado: nao".
Isso e' deliberado: para poder responder "quando mudou e para onde" sem
guardar historico de diff (que cresce sem limite e nao e' auditavel), a
tabela guarda o estado. O historico de "quando" vem do `observed_at` das
linhas, e o diff sempre compara com a linha imediatamente anterior.

Por que uma tabela nova e nao uma coluna em `integration_systems` (DA-49):
o catalogo guarda o que o operador CONFIGURA; o contrato e' o que o SAP
PUBLICA. Sao fatos de Origens diferentes, com um ciclo de escrita diferente
(escrita por polling automatizado, nao por mao humana) e com um dono diferente
(CI versus operador). Misturar os dois faria a DA-49 perder a semantica de
"fonte da verdade do cadastro" e criaria lost update entre o polling e
alguem que edita a tela admin ao mesmo tempo.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class SystemContract(Base):
    """Um contrato observado de um sistema integrado.

    `fingerprint` e' o sha256 da forma canonica do contrato
    (app/contracts/model.py::Contract.canonical_json). Nao e' decodificavel
    de volta para o contrato, mas identifica univocamente qual versao do
    contrato e' esta -- e' o que permite saber "mudou" sem comparar
    estrutura, e o que o gate/CLI usa para dizer se o baseline esta
    defasado.
    """

    __tablename__ = "system_contracts"
    __table_args__ = (
        # Uma linha por observacao. A BASELINE e' a linha de maior
        # `observed_at`; nada e' sobrescrito, entao "o que mudou na
        # segunda-feira" continua respondivel depois.
        UniqueConstraint("system_key", "observed_at", name="uq_system_contracts_key_observed"),
        Index("ix_system_contracts_key_observed", "system_key", "observed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: system_key do catalogo DA-49 (`integration_systems.system_key`).
    #: Sem FK de proposito: o detector tambem roda contra sistemas que so
    #: existem no .env, e perder a observacao por causa de FK seria perder
    #: justamente o sinal que indica que o cadastro esta errado.
    system_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: Literal do pipeline (odata/rfc/...), como em `integration_systems`.
    connector_type: Mapped[str] = mapped_column(String(32), nullable=False)
    contract_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: O contrato normalizado completo. JSONB porque e' escrito e relido
    #: por codigo, nunca por query relacional; e' o que permite
    #: recalcular um diff de um baseline antigo se a taxonomia de severidade
    #: mudar.
    contract: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    entity_count: Mapped[int] = mapped_column(nullable=False, default=0)
    property_count: Mapped[int] = mapped_column(nullable=False, default=0)
    #: Status do report que originou a linha: clean/drift/first_observation.
    #: `unverified` NUNCA vira linha: nao ha contrato novo para gravar, e
    #: gravar o vazio destruiria o baseline anterior sem nenhum ganho.
    observation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<SystemContract key={self.system_key} kind={self.contract_kind} "
            f"fp={self.fingerprint[:12]} at={self.observed_at.isoformat()}>"
        )


def latest_contract(session: Any, system_key: str) -> SystemContract | None:
    """Baseline atual: a observacao mais recente de um sistema.

    `None` = primeira vez. O chamador traduz isso para
    `ObservationStatus.FIRST_OBSERVATION`; e' o `None` que impede
    "sem drift" sem comparacao.

    Erro de banco e' tratado pelo chamador (`observe.py::_load_baseline`), que
    loga e devolve `None` (nao propaga excecao). Aqui, `None` e' exclusivamente
    "nao encontrado".
    """
    statement = (
        select(SystemContract)
        .where(SystemContract.system_key == system_key)
        .order_by(SystemContract.observed_at.desc())
        .limit(1)
    )
    return session.execute(statement).scalar_one_or_none()


def previous_contract(session: Any, system_key: str, before: datetime) -> SystemContract | None:
    """Observacao imediatamente anterior a `before` (para "quando mudou")."""
    statement = (
        select(SystemContract)
        .where(SystemContract.system_key == system_key)
        .where(SystemContract.observed_at < before)
        .order_by(SystemContract.observed_at.desc())
        .limit(1)
    )
    return session.execute(statement).scalar_one_or_none()


def contract_json(row: SystemContract) -> Mapping[str, Any]:
    return row.contract or {}
