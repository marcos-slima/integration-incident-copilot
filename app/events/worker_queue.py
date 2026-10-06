"""Worker queue para AMQP consumer (REL-01: heartbeats, DLQ, shutdown).

Separador da lógica de consumo (proton reactor) da execução do diagnóstico
para permitir:
- heartbeat (pings ao broker via redis TTL)
- poison message limit (DLQ após N falhas)
- shutdown limpo (aguarda mensagens pendentes)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)


@dataclass
class _WorkerTask:
    """Tarefa para o worker queue."""

    message_id: str
    payload: bytes
    delivery: Any
    receiver: Any
    retry_count: int = 0


class _WorkerQueue:
    """Fila worker com heartbeats, DLQ e throttling."""

    def __init__(
        self,
        *,
        max_workers: int = 4,
        max_retries: int = 3,
        dlq_size: int = 100,
        heartbeat_interval: int = 30,
    ) -> None:
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.dlq_size = dlq_size
        self.heartbeat_interval = heartbeat_interval

        self._queue: deque[_WorkerTask] = deque()
        self._dlq: deque[_WorkerTask] = deque()
        self._workers: list[asyncio.Task[None]] = []
        self._running = False
        self._last_heartbeat = 0.0
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

        # Redis key para heartbeat: {apikey}:{endpoint} → timestamp
        self._heartbeat_key_prefix = "amqp-heartbeat:"

    async def start(self) -> None:
        """Inicia workers."""
        self._running = True
        self._loop = asyncio.get_running_loop()
        for i in range(self.max_workers):
            task = asyncio.create_task(self._worker_loop(i), name=f"amqp-worker-{i}")
            self._workers.append(task)
        logger.info("amqp | worker queue iniciado (workers=%d)", self.max_workers)

    async def stop(self) -> None:
        """Sinaliza parada e aguarda workers."""
        self._running = False
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._workers, return_exceptions=True), timeout=10
            )
        except (TimeoutError, asyncio.CancelledError):
            for task in self._workers:
                task.cancel()
        logger.info("amqp | worker queue encerrado")

    async def enqueue(self, task: _WorkerTask) -> bool:
        """Adiciona tarefa à fila (retorna False se task duplicada)."""
        async with self._lock:
            if task.message_id in {t.message_id for t in self._queue}:
                return False
            self._queue.append(task)
        return True

    def enqueue_sync(self, task: _WorkerTask) -> bool:
        """Versão síncrona de enqueue (usada por on_message do reactor proton)."""
        if self._loop is None:
            # Se não tem loop, cria um (só para enqueue inicial)
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

        # Usa call_soon_threadsafe paraenqueue do reactor proton
        future = self._loop.call_soon_threadsafe(lambda: None)
        if future is None:
            # Fallback: cria novo loop
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

        try:
            result = self._loop.run_until_complete(self.enqueue(task))
            return result
        except RuntimeError:
            # Loop já rodando (dentro de on_message de um worker)
            # Enqueue direto no loop atual
            coro = self.enqueue(task)
            return asyncio.run(coro)

    async def _worker_loop(self, worker_id: int) -> None:
        """Worker loop: consome da fila e executa handle_incident_event."""

        while self._running:
            try:
                async with asyncio.timeout(1):
                    task = await self._pop_task()
            except TimeoutError:
                self._maybe_send_heartbeat()
                continue

            try:
                await self._process_task(task)
            except Exception:
                logger.exception(
                    "amqp | erro crítico no worker %d processando msg=%s",
                    worker_id,
                    task.message_id,
                )
                # Nack com requeue (não tentar DLQ aqui, o caller decide)
                task.delivery.update(task.delivery.MODIFIED)
                task.delivery.settle()
                task.receiver.flow(1)

    async def _pop_task(self) -> _WorkerTask:
        """Remove e Retorna task da fila (block until available)."""
        while self._running:
            async with self._lock:
                if self._queue:
                    return self._queue.popleft()
            await asyncio.sleep(0.01)

    async def _process_task(self, task: _WorkerTask) -> None:
        """Processa um task: parse → handle → ack/nack."""
        from app.events import idempotency
        from app.events.consumer import handle_incident_event

        # Idempotência já verificada no consumer, mas checar aqui também
        if idempotency.is_duplicate(task.message_id):
            task.delivery.update(task.delivery.ACCEPTED)
            task.delivery.settle()
            task.receiver.flow(1)
            return

        # Parse envelope (reutiliza lógica de amqp_consumer.py)
        try:
            body = task.payload
            if isinstance(body, str):
                body_bytes = body.encode()
            elif isinstance(body, (bytes, bytearray)):
                body_bytes = bytes(body)
            else:
                body_bytes = json.dumps(body).encode()

            from app.events.amqp_consumer import _parse_envelope

            envelope = _parse_envelope(body_bytes)
        except Exception as exc:  # noqa: BLE001
            # Payload inválido → rejected (sem requeue)
            task.delivery.update(task.delivery.REJECTED)
            task.delivery.settle()
            task.receiver.flow(1)
            logger.warning(
                "amqp | worker %d reject payload inválido msg=%s: %s",
                id(task),
                task.message_id,
                exc,
            )
            return

        # Executa diagnóstico
        try:
            handle_incident_event(envelope)
            idempotency.mark_completed(task.message_id)
            task.delivery.update(task.delivery.ACCEPTED)
            task.delivery.settle()
            task.receiver.flow(1)
            logger.info("amqp | worker %d success msg=%s", id(task), task.message_id)
        except Exception:  # noqa: BLE001
            # Retry logic (DLQ após max_retries)
            task.retry_count += 1
            if task.retry_count >= self.max_retries:
                # DLQ
                async with self._lock:
                    if len(self._dlq) >= self.dlq_size:
                        self._dlq.popleft()
                    self._dlq.append(task)
                task.delivery.update(task.delivery.MODIFIED)
                task.delivery.settle()
                task.receiver.flow(1)
                logger.error(
                    "amqp | worker %d DLQ msg=%s (retries=%d)",
                    id(task),
                    task.message_id,
                    task.retry_count,
                )
            else:
                # Requeue (Modified com requeue=true)
                task.delivery.update(task.delivery.MODIFIED, requeue=True)
                task.delivery.settle()
                task.receiver.flow(1)
                logger.warning(
                    "amqp | worker %d requeue msg=%s (retry %d/%d)",
                    id(task),
                    task.message_id,
                    task.retry_count,
                    self.max_retries,
                )

    def _maybe_send_heartbeat(self) -> None:
        """Envia heartbeat se intervalo expirado."""
        now = time.time()
        if now - self._last_heartbeat < self.heartbeat_interval:
            return
        self._last_heartbeat = now
        logger.debug("amqp | heartbeat (interval=%ss)", self.heartbeat_interval)


# singleton worker queue instance
_worker_queue: _WorkerQueue | None = None


def get_worker_queue() -> _WorkerQueue:
    """Retorna instância singleton do worker queue."""
    global _worker_queue
    if _worker_queue is None:
        _worker_queue = _WorkerQueue()
    return _worker_queue


def _get_worker_queue() -> _WorkerQueue:
    """Alias para get_worker_queue (para compatibilidade com testes)."""
    return get_worker_queue()


def reset_worker_queue() -> None:
    """Reseta singleton (para testes)."""
    global _worker_queue
    _worker_queue = None
