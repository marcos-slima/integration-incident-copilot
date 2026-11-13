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

Processamento (validacao 2026-10-06, REL-01/N-04):
    O reactor do proton e single-thread e NAO pode bloquear: heartbeat do
    link, entrega de credito e disposition dependem dele. O diagnostico
    (ate diagnosis_timeout_seconds) roda fora dele:

    * com REDIS_URL: a mensagem vira job RQ (app/queue.py::enqueue_incident_event,
      o mesmo caminho do webhook /events/incident). Enqueue ok -> ACCEPTED;
      a deduplicacao e o retry ficam com o worker RQ.
    * sem REDIS_URL: ThreadPoolExecutor com max_workers = AMQP_PREFETCH. A
      conclusao volta ao reactor por EventInjector (ApplicationEvent
      "diagnosis_done"); so ali o Delivery e liquidado e o credito devolvido
      - objetos do proton nunca sao tocados fora da thread do reactor.

    Falha -> MODIFIED com delivery-failed (o broker incrementa delivery-count
    e reentrega). Ao atingir AMQP_MAX_REDELIVERIES a mensagem e REJECTED,
    que no Solace/Event Mesh vai para a DMQ configurada na fila - a
    "mensagem envenenada" sai do loop.
    Parada: um timer de 1 s no reactor checa o stop_flag e fecha o link,
    mesmo sem mensagens chegando.
"""

from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import quote

from app.config import settings
from app.events import idempotency
from app.models import IncidentEventEnvelope

logger = logging.getLogger(__name__)

_DONE_EVENT = "diagnosis_done"
_STOP_CHECK_SECONDS = 1.0

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


def _body_bytes(body_raw: Any) -> bytes:
    if isinstance(body_raw, str):
        return body_raw.encode()
    if isinstance(body_raw, (bytes, bytearray)):
        return bytes(body_raw)
    return json.dumps(body_raw).encode()


def _connection_url() -> str:
    # quote(): credenciais com "@", ":" ou "/" quebrariam o parse da URL
    return (
        f"amqps://{quote(settings.amqp_username, safe='')}"
        f":{quote(settings.amqp_password, safe='')}"
        f"@{settings.amqp_host}:{settings.amqp_port}"
    )


def _ssl_domain():
    from proton import SSLDomain

    ssl_domain = SSLDomain(SSLDomain.MODE_CLIENT)
    ssl_domain.set_peer_authentication(SSLDomain.VERIFY_PEER)
    return ssl_domain


def _run_diagnosis(envelope: IncidentEventEnvelope) -> bool:
    """Roda no executor (fora do reactor). True = sucesso."""
    from app.events.consumer import handle_incident_event

    try:
        handle_incident_event(envelope)
    except Exception:
        logger.exception("amqp | diagnostico falhou cloudevents.id=%s", envelope.id)
        idempotency.release(idempotency.event_key(envelope))
        return False
    idempotency.mark_completed(idempotency.event_key(envelope))
    return True


def _make_handler(stop_flag: list[bool], executor: ThreadPoolExecutor | None):
    """Constroi o MessagingHandler (import do proton e tardio: dependencia
    opcional). Separado do loop para ser testavel sem broker."""
    from proton import Delivery
    from proton.handlers import MessagingHandler
    from proton.reactor import ApplicationEvent, EventInjector

    class _IncidentHandler(MessagingHandler):
        def __init__(self) -> None:
            super().__init__(prefetch=0, auto_accept=False)
            self._receiver = None
            self._connection = None
            self._injector = None

        # -- ciclo de vida -------------------------------------------------
        def on_start(self, event):
            self._connection = event.container.connect(
                _connection_url(),
                ssl_domain=_ssl_domain(),
                reconnect=False,  # reconexão gerenciada no _blocking_consume_loop
            )
            # create_receiver() nao aceita "credit": com prefetch=0 o credito
            # inicial e emitido via flow() - sem isso o broker nao entrega nada.
            self._receiver = event.container.create_receiver(self._connection, settings.amqp_queue)
            self._receiver.flow(settings.amqp_prefetch)
            self._injector = EventInjector()
            event.container.selectable(self._injector)
            event.container.schedule(_STOP_CHECK_SECONDS, self)
            logger.info(
                "amqp | conectado (AMQP 1.0) host=%s queue=%s",
                settings.amqp_host,
                settings.amqp_queue,
            )

        def on_timer_task(self, event):
            if stop_flag[0]:
                self._close()
            else:
                event.container.schedule(_STOP_CHECK_SECONDS, self)

        def _close(self) -> None:
            if self._receiver is not None:
                self._receiver.close()
            if self._connection is not None:
                self._connection.close()
            if self._injector is not None:
                self._injector.close()

        # -- disposition (sempre na thread do reactor) ------------------
        def _settle(self, delivery, outcome) -> None:
            if outcome == Delivery.MODIFIED:
                # delivery-failed: o broker conta a tentativa (delivery-count)
                delivery.local.failed = True
            delivery.update(outcome)
            delivery.settle()
            if self._receiver is not None and not stop_flag[0]:
                self._receiver.flow(1)

        # -- mensagens ---------------------------------------------------
        def on_message(self, event) -> None:
            msg = event.message
            delivery = event.delivery
            body = _body_bytes(msg.body)
            logger.info("amqp | mensagem recebida id=%s size=%d", msg.id, len(body))

            envelope = _parse_envelope(body)
            if envelope is None:
                logger.warning("amqp | mensagem rejeitada (payload inválido) id=%s", msg.id)
                self._settle(delivery, Delivery.REJECTED)
                return

            # delivery_count conta as entregas ANTERIORES (0 na primeira).
            tentativas = int(getattr(msg, "delivery_count", 0) or 0)
            if tentativas >= settings.amqp_max_redeliveries:
                logger.error(
                    "amqp | cloudevents.id=%s falhou %d vezes - REJECTED (vai para a DMQ do broker)",
                    envelope.id,
                    tentativas,
                )
                self._settle(delivery, Delivery.REJECTED)
                return

            if settings.redis_url:
                self._enqueue_rq(envelope, delivery)
                return

            if idempotency.is_duplicate(idempotency.event_key(envelope)):
                logger.info("amqp | mensagem duplicada descartada cloudevents.id=%s", envelope.id)
                self._settle(delivery, Delivery.ACCEPTED)
                return

            injector = self._injector

            def _done(fut) -> None:
                ok = (not fut.cancelled()) and fut.exception() is None and fut.result() is True
                injector.trigger(ApplicationEvent(_DONE_EVENT, delivery=delivery, subject=ok))

            executor.submit(_run_diagnosis, envelope).add_done_callback(_done)

        def _enqueue_rq(self, envelope: IncidentEventEnvelope, delivery) -> None:
            from app.queue import enqueue_incident_event

            try:
                job_id = enqueue_incident_event(envelope.model_dump(mode="json"))
            except Exception:
                logger.exception("amqp | falha ao enfileirar no RQ cloudevents.id=%s", envelope.id)
                self._settle(delivery, Delivery.MODIFIED)
                return
            logger.info("amqp | enfileirado no RQ cloudevents.id=%s job=%s", envelope.id, job_id)
            self._settle(delivery, Delivery.ACCEPTED)

        def on_diagnosis_done(self, event) -> None:
            self._settle(event.delivery, Delivery.ACCEPTED if event.subject else Delivery.MODIFIED)

        # -- erros -------------------------------------------------------
        def on_connection_error(self, event) -> None:
            logger.error("amqp | erro de conexão AMQP 1.0: %s", event.connection.condition)

        def on_transport_error(self, event) -> None:
            logger.error("amqp | erro de transporte AMQP 1.0: %s", event.transport.condition)

        def on_disconnected(self, event) -> None:
            if not stop_flag[0]:
                logger.warning("amqp | desconectado — reconexão gerenciada pelo loop externo")

    return _IncidentHandler()


def _blocking_consume_loop(stop_flag: list[bool]) -> None:
    """Loop de consumo AMQP 1.0 (roda numa thread via run_in_executor).

    stop_flag é uma lista de um elemento [False] — mutável por referência,
    permite que a coroutine asyncio sinalize parada para esta thread.
    """
    try:
        from proton.reactor import Container
    except ImportError as exc:
        logger.error(
            "amqp | python-qpid-proton não instalado. "
            "Adicione 'python-qpid-proton' ao pyproject.toml e reinstale. "
            "erro=%s",
            exc,
        )
        return

    import time

    executor = (
        None
        if settings.redis_url
        else ThreadPoolExecutor(
            max_workers=max(1, settings.amqp_prefetch), thread_name_prefix="amqp-diag"
        )
    )
    try:
        while not stop_flag[0]:
            try:
                Container(_make_handler(stop_flag, executor)).run()
            except Exception as exc:  # noqa: BLE001
                logger.error("amqp | erro no container AMQP 1.0: %s", exc)
            if not stop_flag[0]:
                logger.info(
                    "amqp | aguardando %ds antes de reconectar", settings.amqp_reconnect_delay
                )
                time.sleep(settings.amqp_reconnect_delay)
    finally:
        if executor is not None:
            # Diagnosticos em curso terminam; os nao liquidados sao reentregues
            # pelo broker quando o link fecha (at-least-once).
            executor.shutdown(wait=False, cancel_futures=True)
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
        """Inicia o consumidor em background via asyncio.run_in_executor().

        run_in_executor() retorna um asyncio.Future, nao uma coroutine.
        create_task() exige coroutine, entao encapsulamos o Future num
        wrapper async para que a task seja agendada corretamente.
        """
        if not settings.amqp_enabled:
            logger.info("amqp | consumidor desabilitado (AMQP_ENABLED=false)")
            return
        self._stop_flag = [False]
        loop = asyncio.get_running_loop()

        async def _run() -> None:
            await loop.run_in_executor(None, _blocking_consume_loop, self._stop_flag)

        self._task = asyncio.create_task(_run(), name="amqp-consumer-amqp10")
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
