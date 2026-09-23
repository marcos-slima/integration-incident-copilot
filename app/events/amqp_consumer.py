"""DA-32 — Consumidor AMQP 1.0 assíncrono via Solace Cloud.

DA-40: migração de aiormq (AMQP 0.9.1 — protocolo RabbitMQ) para
python-qpid-proton (AMQP 1.0 — protocolo Solace/SAP Event Mesh).

Problema original (DA-32): aiormq implementa AMQP 0.9.1. O Solace Cloud
(SAP Event Mesh) usa exclusivamente AMQP 1.0. A conexão parecia funcionar
em ambiente de desenvolvimento controlado (handshake TLS bem-sucedido,
banner AMQP trocado), mas falhava silenciosamente ao tentar operações de
producer/consumer (queue_declare, basic_consume) pois os frames AMQP 0.9.1
são inválidos para um broker AMQP 1.0 — o Solace fecha a conexão ou ignora
os frames sem erro explícito.

Solução (DA-40): python-qpid-proton (lib oficial Apache Qpid Proton,
usada internamente pelo SAP Event Mesh e pelo próprio Solace SDK) com
wrapper asyncio via asyncio.run_in_executor() — a API qpid-proton é
síncrona/blocking, então roda em thread pool sem bloquear o event loop.

Configuração (via variáveis de ambiente / .env):
    AMQP_ENABLED=true
    AMQP_HOST=mr-connection-kytjcnxk2he.messaging.solace.cloud
    AMQP_PORT=5671
    AMQP_USERNAME=solace-cloud-client
    AMQP_PASSWORD=<secret>
    AMQP_QUEUE=integration/incidents  # Topic Endpoint ou Queue no Solace
    AMQP_PREFETCH=1                   # créditos de link (QoS AMQP 1.0)
    AMQP_RECONNECT_DELAY=5            # segundos entre reconexões
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from typing import Any

from app.config import settings
from app.events.consumer import handle_incident_event
from app.models import IncidentEventEnvelope

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_envelope(body: bytes) -> IncidentEventEnvelope | None:
    """Converte payload JSON em IncidentEventEnvelope.

    Retorna None e loga o erro se o payload for inválido — a mensagem será
    rejected (sem requeue) em vez de ficar em loop infinito.
    """
    try:
        data: dict[str, Any] = json.loads(body)
        return IncidentEventEnvelope(**data)
    except Exception as exc:  # noqa: BLE001
        logger.error("amqp | payload inválido — ignorando mensagem: %s", exc)
        return None


def _blocking_consume_loop(stop_flag: list[bool]) -> None:
    """Loop de consumo AMQP 1.0 síncrono (roda em thread pool via executor).

    Usa python-qpid-proton (Apache Qpid Proton) — implementação de referência
    AMQP 1.0, compatível com Solace Cloud / SAP Event Mesh.

    stop_flag é uma lista de um elemento [False] — mutável por referência,
    permite que a coroutine asyncio sinalize parada para esta thread sem
    mecanismo de sincronização mais complexo.
    """
    try:
        from proton import Message, SSLDomain
        from proton.handlers import MessagingHandler
        from proton.reactor import Container
    except ImportError as exc:
        logger.error(
            "amqp | python-qpid-proton não instalado. "
            "Adicione 'python-qpid-proton' ao pyproject.toml e reinstale. "
            "erro=%s",
            exc,
        )
        return

    delay = settings.amqp_reconnect_delay

    class _IncidentHandler(MessagingHandler):
        """Handler AMQP 1.0: conecta, cria receiver com crédito controlado,
        processa mensagens e faz ack/nack via AMQP 1.0 disposition frames."""

        def __init__(self) -> None:
            super().__init__(prefetch=0, auto_accept=False)
            self._receiver = None

        def on_start(self, event):
            ssl_domain = SSLDomain(SSLDomain.MODE_CLIENT)
            ssl_domain.set_peer_authentication(SSLDomain.VERIFY_PEER)

            url = (
                f"amqps://{settings.amqp_username}:{settings.amqp_password}"
                f"@{settings.amqp_host}:{settings.amqp_port}"
            )
            conn = event.container.connect(
                url,
                ssl_domain=ssl_domain,
                reconnect=False,  # reconexão gerenciada no _blocking_consume_loop
            )
            # Receiver com credit controlado (equivalente ao prefetch AMQP 0.9.1)
            self._receiver = event.container.create_receiver(
                conn,
                settings.amqp_queue,
                credit=settings.amqp_prefetch,
            )
            logger.info(
                "amqp | conectado (AMQP 1.0) host=%s queue=%s",
                settings.amqp_host,
                settings.amqp_queue,
            )

        def on_message(self, event) -> None:
            msg: Message = event.message
            delivery = event.delivery

            body_raw = msg.body
            if isinstance(body_raw, str):
                body_bytes = body_raw.encode()
            elif isinstance(body_raw, (bytes, bytearray)):
                body_bytes = bytes(body_raw)
            else:
                body_bytes = json.dumps(body_raw).encode()

            logger.info(
                "amqp | mensagem recebida id=%s size=%d",
                msg.id,
                len(body_bytes),
            )

            envelope = _parse_envelope(body_bytes)
            if envelope is None:
                # payload inválido → rejected (AMQP 1.0 Rejected disposition)
                delivery.update(delivery.REJECTED)
                delivery.settle()
                logger.warning("amqp | mensagem rejeitada (payload inválido) id=%s", msg.id)
                return

            try:
                handle_incident_event(envelope)
                # Accepted → ack (AMQP 1.0 Accepted disposition)
                delivery.update(delivery.ACCEPTED)
                delivery.settle()
                logger.info("amqp | mensagem processada id=%s", msg.id)
            except Exception:
                logger.exception("amqp | erro ao processar mensagem id=%s", msg.id)
                # Modified → nack com requeue (AMQP 1.0 Modified disposition)
                delivery.update(delivery.MODIFIED)
                delivery.settle()

            # Emite crédito para a próxima mensagem
            if self._receiver:
                self._receiver.flow(1)

            # Verifica flag de parada
            if stop_flag[0]:
                event.receiver.close()
                event.connection.close()

        def on_connection_error(self, event) -> None:
            logger.error(
                "amqp | erro de conexão AMQP 1.0: %s",
                event.connection.condition,
            )

        def on_transport_error(self, event) -> None:
            logger.error(
                "amqp | erro de transporte AMQP 1.0: %s",
                event.transport.condition,
            )

        def on_disconnected(self, event) -> None:
            if not stop_flag[0]:
                logger.warning("amqp | desconectado — reconexão gerenciada pelo loop externo")

    while not stop_flag[0]:
        try:
            container = Container(_IncidentHandler())
            container.run()  # blocking — roda até on_disconnected ou erro
        except Exception as exc:  # noqa: BLE001
            logger.error("amqp | erro no container AMQP 1.0: %s", exc)

        if not stop_flag[0]:
            logger.info("amqp | aguardando %ds antes de reconectar", delay)
            import time
            time.sleep(delay)

    logger.info("amqp | loop de consumo encerrado")


# ---------------------------------------------------------------------------
# API pública para o lifespan
# ---------------------------------------------------------------------------


class AmqpConsumerTask:
    """Gerencia o ciclo de vida do consumidor AMQP 1.0 como background task."""

    def __init__(self) -> None:
        self._stop_flag: list[bool] = [False]
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Inicia o consumidor em background via asyncio.run_in_executor()."""
        if not settings.amqp_enabled:
            logger.info("amqp | consumidor desabilitado (AMQP_ENABLED=false)")
            return
        self._stop_flag = [False]
        loop = asyncio.get_event_loop()
        self._task = asyncio.create_task(
            loop.run_in_executor(None, _blocking_consume_loop, self._stop_flag),
            name="amqp-consumer-amqp10",
        )
        logger.info("amqp | background task AMQP 1.0 iniciada (python-qpid-proton)")

    async def stop(self) -> None:
        """Sinaliza parada e aguarda a task encerrar."""
        self._stop_flag[0] = True
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                logger.warning("amqp | task encerrada forçadamente no shutdown")
        logger.info("amqp | consumidor AMQP 1.0 encerrado")


amqp_consumer = AmqpConsumerTask()
