"""DA-69: checkpointer do grafo com redacao de PII na persistencia.

Dois problemas fechados aqui:

1. **PII em claro no checkpoint.** O estado do grafo guarda `description`,
   `logs`, `payload` e `connector_data` crus - a redacao (DA-30) so
   acontece ao montar o prompt, e o fluxo (conector, RAG) precisa do texto
   original. Redigir o estado de entrada mudaria o comportamento do
   diagnostico. Em vez disso, `RedactingCheckpointMixin` redige SO o que o
   checkpointer grava: cada valor de canal passa por
   `redact_preserving_types` no `put`/`put_writes`. Em memoria, durante a
   execucao, nada muda.

   Consequencia declarada: um diagnostico RETOMADO le o estado redigido
   (e-mail/CPF ja substituidos). E o preco de nunca gravar PII.

2. **Construcao do PostgresSaver.** `PostgresSaver.from_conn_string` e um
   context manager (o get_graph anterior chamava `.setup()` no gerador,
   nao no saver) e espera URL libpq, enquanto `DATABASE_URL` usa o dialeto
   SQLAlchemy (`postgresql+asyncpg://`). `build_postgres_checkpointer`
   normaliza a URL e abre uma conexao propria, de vida longa.

   Limite: uma conexao unica compartilhada pelas threads do pool do grafo
   (o PostgresSaver serializa o acesso com lock); sem reconexao automatica.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from app.redaction import redact_pii_text

_DIALECT_PREFIXES = (
    "postgresql+asyncpg://",
    "postgresql+psycopg2://",
    "postgresql+psycopg://",
    "postgres://",
)


def libpq_url(raw: str) -> str:
    """URL SQLAlchemy -> URL libpq (`postgresql://`)."""
    for prefix in _DIALECT_PREFIXES:
        if raw.startswith(prefix):
            return "postgresql://" + raw[len(prefix) :]
    return raw


def redact_preserving_types(value: Any) -> Any:
    """Redige PII em qualquer string dentro de `value`, preservando o TIPO
    de containers, dataclasses e modelos Pydantic.

    Diferente de `redact_pii_deep` (mascara do Langfuse), que converte
    dataclass/Pydantic em dict: aqui o valor volta do checkpoint para o
    grafo numa retomada, e os nos esperam os tipos originais (ex.:
    `ConnectorResult.is_mock`)."""
    if isinstance(value, str):
        return redact_pii_text(value)
    if isinstance(value, dict):
        return type(value)((k, redact_preserving_types(v)) for k, v in value.items())
    if isinstance(value, list):
        return [redact_preserving_types(v) for v in value]
    if isinstance(value, tuple) and not hasattr(value, "_fields"):
        return tuple(redact_preserving_types(v) for v in value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        changes = {
            f.name: redact_preserving_types(getattr(value, f.name))
            for f in dataclasses.fields(value)
            if f.init
        }
        return dataclasses.replace(value, **changes)
    if hasattr(value, "model_copy") and hasattr(type(value), "model_fields"):
        update = {
            name: redact_preserving_types(getattr(value, name)) for name in type(value).model_fields
        }
        return value.model_copy(update=update)
    return value


class RedactingCheckpointMixin:
    """Redige PII nos VALORES dos canais antes de o checkpointer gravar.

    Atua no `put`/`put_writes` (e variantes async), nao no serializer: o
    PostgresSaver grava `str`/`int`/`bool`/`None` INLINE no jsonb do
    checkpoint, sem passar pelo serde - redigir so no serializer deixaria
    `description` em claro no banco. A estrutura de controle do checkpoint
    (ids, `channel_versions`) nunca e tocada: as versoes tem sequencias
    longas de digitos que a regra de numero de IDoc reescreveria,
    corrompendo o checkpoint."""

    def put(self, config, checkpoint, metadata, new_versions):
        return super().put(config, _redacted_checkpoint(checkpoint), metadata, new_versions)

    def put_writes(self, config, writes, task_id, task_path=""):
        return super().put_writes(config, _redacted_writes(writes), task_id, task_path)

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await super().aput(config, _redacted_checkpoint(checkpoint), metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return await super().aput_writes(config, _redacted_writes(writes), task_id, task_path)


def _redacted_checkpoint(checkpoint):
    copy = dict(checkpoint)
    copy["channel_values"] = {
        channel: redact_preserving_types(value)
        for channel, value in checkpoint.get("channel_values", {}).items()
    }
    return copy


def _redacted_writes(writes):
    return [(channel, redact_preserving_types(value)) for channel, value in writes]


class RedactingMemorySaver(RedactingCheckpointMixin, InMemorySaver):
    """InMemorySaver com redacao - usado nos testes e em execucao local."""


def build_postgres_checkpointer(database_url: str):
    """PostgresSaver com redacao e conexao de vida longa.

    Import tardio: so quem liga CHECKPOINTING_ENABLED precisa de psycopg."""
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg import Connection
    from psycopg.rows import dict_row

    class RedactingPostgresSaver(RedactingCheckpointMixin, PostgresSaver):
        pass

    conn = Connection.connect(
        libpq_url(database_url), autocommit=True, prepare_threshold=0, row_factory=dict_row
    )
    saver = RedactingPostgresSaver(conn)
    saver.setup()
    return saver
