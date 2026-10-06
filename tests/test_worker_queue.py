"""Testes para worker queue (REL-01: heartbeats, DLQ, shutdown)."""

from unittest.mock import MagicMock

import pytest

from app.events.worker_queue import _WorkerQueue, get_worker_queue, reset_worker_queue


@pytest.fixture(autouse=True)
def clear_worker_queue() -> None:
    """Reseta worker queue antes de cada teste."""
    reset_worker_queue()
    yield
    reset_worker_queue()


class TestWorkerQueue:
    """Testes para _WorkerQueue."""

    def test_init(self) -> None:
        """Worker queue inicia com configuração padrão."""
        queue = _WorkerQueue()
        assert queue.max_workers == 4
        assert queue.max_retries == 3
        assert queue.dlq_size == 100
        assert queue.heartbeat_interval == 30

    def test_custom_config(self) -> None:
        """Worker queue aceita configuração customizada."""
        queue = _WorkerQueue(max_workers=8, max_retries=5, dlq_size=200, heartbeat_interval=60)
        assert queue.max_workers == 8
        assert queue.max_retries == 5
        assert queue.dlq_size == 200
        assert queue.heartbeat_interval == 60

    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        """Start e stop do worker queue funcionam."""
        queue = _WorkerQueue(max_workers=2)
        await queue.start()
        assert queue._running is True
        assert len(queue._workers) == 2
        await queue.stop()
        assert queue._running is False

    @pytest.mark.asyncio
    async def test_enqueue_task(self) -> None:
        """Enqueue adiciona tarefas à fila."""
        queue = _WorkerQueue(max_workers=1)
        await queue.start()

        mock_delivery = MagicMock()
        mock_receiver = MagicMock()

        task = MagicMock()
        task.message_id = "task-001"
        task.payload = b"test"
        task.delivery = mock_delivery
        task.receiver = mock_receiver

        result = await queue.enqueue(task)
        assert result is True

        await queue.stop()

    @pytest.mark.asyncio
    async def test_enqueue_duplicate_task(self) -> None:
        """Enqueue rejeita tarefas duplicadas."""
        queue = _WorkerQueue(max_workers=1)
        await queue.start()

        mock_delivery = MagicMock()
        mock_receiver = MagicMock()

        task1 = MagicMock()
        task1.message_id = "task-001"
        task1.payload = b"test"
        task1.delivery = mock_delivery
        task1.receiver = mock_receiver

        task2 = MagicMock()
        task2.message_id = "task-001"
        task2.payload = b"test"
        task2.delivery = MagicMock()
        task2.receiver = MagicMock()

        result1 = await queue.enqueue(task1)
        result2 = await queue.enqueue(task2)

        assert result1 is True
        assert result2 is False

        await queue.stop()

    def test_get_worker_queue_singleton(self) -> None:
        """get_worker_queue retorna instância singleton."""
        queue1 = get_worker_queue()
        queue2 = get_worker_queue()
        assert queue1 is queue2

    def test_reset_worker_queue(self) -> None:
        """reset_worker_queue limpa singleton."""
        queue1 = get_worker_queue()
        reset_worker_queue()
        queue2 = get_worker_queue()
        assert queue1 is not queue2
