"""Ingestao orientada a evento (DA-23) - ver app/events/consumer.py.

Exports:
    - worker_queue: _WorkerQueue, get_worker_queue, _get_worker_queue (REL-01)
"""

from app.events.worker_queue import (
    _get_worker_queue,
    _WorkerQueue,
    get_worker_queue,
)

__all__ = ["_WorkerQueue", "_get_worker_queue", "get_worker_queue"]
