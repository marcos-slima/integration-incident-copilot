"""DA-32 — Consumidor AMQP 1.0 assíncrono via Solace Cloud (aiormq).

Conecta-se ao broker usando AMQP over TLS (porta 5671) e processa
mensagens CloudEvents que chegam em formato JSON.  Cada mensagem é
encaminhada para ``handle_incident_event``, a mesma função usada pelo
consumidor HTTP (DA-23), garantindo rota de processamento única.

Configuração (via variáveis de ambiente / .env):
    AMQP_ENABLED=true
    AMQP_HOST=mr-connection-kytjcnxk2he.messaging.solace.cloud
    AMQP_PORT=5671
    AMQP_USERNAME=solace-cloud-client
    AMQP_PASSWORD=<secret>
    AMQP_QUEUE=integration/incidents  # fila ou topic endpoint no Solace
    AMQP_PREFETCH=1                   # créditos de link (QoS)
    AMQP_RECONNECT_DELAY=5            # segundos entre reconexões
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from typing import Any

import aiormq
import aiormq.abc

from app.config import settings
from app.events.consumer import handle_incident_event
from app.models import IncidentEventEnvelope

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_tls_context() -> ssl.SSLContext:
    """Contexto TLS padrão para porta 5671 (AMQPS)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def _parse_envelope(body: bytes) -> IncidentEventEnvelope | None:
    """Converte payload JSON em IncidentEventEnvelope.

    Retorna None e loga o erro se o payload for inválido — a mensagem será
    rejected (dead-letter) em vez de ficar em loop infinito.
    """
    try:
        data: dict[str, Any] = json.loads(body)
        return IncidentEventEnvelope(**data)
    except Exception as exc:  # noqa: BLE001
        logger.error("amqp | payload inválido — ignorando mensagem: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Consumer principal
# ---------------------------------------------------------------------------


async def _process_message(message: aiormq.abc.DeliveredMessage) -> None:
    """Processa uma única mensagem AMQP."""
    delivery_tag = message.delivery.delivery_tag
    body: bytes = message.body

    logger.info(
        "amqp | mensagem recebida delivery_tag=%s size=%d",
        delivery_tag,
        len(body),
    )

    envelope = _parse_envelope(body)
    if envelope is None:
        # payload inválido → reject sem re-enqueue
        await message.channel.basic_reject(delivery_tag=delivery_tag, requeue=False)
        return

    try:
        await asyncio.to_thread(handle_incident_event, envelope)
        await message.channel.basic_ack(delivery_tag=delivery_tag)
        logger.info("amqp | mensagem processada com sucesso delivery_tag=%s", delivery_tag)
    except Exception:
        logger.exception("amqp | erro ao processar mensagem delivery_tag=%s", delivery_tag)
        # requeue=True para tentar novamente (dead-letter após N tentativas no broker)
        await message.channel.basic_nack(delivery_tag=delivery_tag, requeue=True)


async def _consume_loop(stop_event: asyncio.Event) -> None:
    """Loop de consumo com reconexão automática."""
    url = (
        f"amqps://{settings.amqp_username}:{settings.amqp_password}"
        f"@{settings.amqp_host}:{settings.amqp_port}"
    )
    tls_ctx = _build_tls_context()
    delay = settings.amqp_reconnect_delay

    while not stop_event.is_set():
        connection: aiormq.Connection | None = None
        try:
            logger.info(
                "amqp | conectando em %s:%s queue=%s",
                settings.amqp_host,
                settings.amqp_port,
                settings.amqp_queue,
            )
            connection = await aiormq.connect(url, context=tls_ctx)
            channel = await connection.channel()

            # QoS: processar uma mensagem por vez (prefetch_count)
            await channel.basic_qos(prefetch_count=settings.amqp_prefetch)

            # Declara fila passivamente (deve existir no Solace como Topic Endpoint ou Queue)
            await channel.queue_declare(settings.amqp_queue, passive=True)

            await channel.basic_consume(settings.amqp_queue, _process_message, no_ack=False)

            logger.info("amqp | consumindo fila '%s'", settings.amqp_queue)

            # Aguarda até que o stop seja sinalizado ou a conexão caia
            await asyncio.wait(
                [
                    asyncio.ensure_future(stop_event.wait()),
                    asyncio.ensure_future(connection.closing),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if stop_event.is_set():
                logger.info("amqp | shutdown solicitado, encerrando conexão")
                break

            logger.warning("amqp | conexão encerrada pelo broker, reconectando em %ds", delay)

        except asyncio.CancelledError:
            logger.info("amqp | tarefa cancelada")
            break
        except Exception as exc:  # noqa: BLE001
            logger.error("amqp | erro de conexão: %s — reconectando em %ds", exc, delay)
            await asyncio.sleep(delay)
        finally:
            if connection and not connection.is_closed:
                try:
                    await connection.close()
                except Exception:  # noqa: BLE001 S110
                    pass


# ---------------------------------------------------------------------------
# API pública para o lifespan
# ---------------------------------------------------------------------------


class AmqpConsumerTask:
    """Gerencia o ciclo de vida do consumidor AMQP como background task."""

    def __init__(self) -> None:
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Inicia o consumidor em background."""
        if not settings.amqp_enabled:
            logger.info("amqp | consumidor desabilitado (AMQP_ENABLED=false)")
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(
            _consume_loop(self._stop_event),
            name="amqp-consumer",
        )
        logger.info("amqp | background task iniciada")

    async def stop(self) -> None:
        """Sinaliza parada e aguarda a task encerrar."""
        if self._stop_event:
            self._stop_event.set()
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                logger.warning("amqp | task encerrada forçadamente no shutdown")
        logger.info("amqp | consumidor encerrado")


amqp_consumer = AmqpConsumerTask()
