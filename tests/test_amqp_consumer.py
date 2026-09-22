"""Testes para DA-32 — AmqpConsumerTask e helpers."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.events.amqp_consumer import AmqpConsumerTask, _parse_envelope


# ---------------------------------------------------------------------------
# _parse_envelope
# ---------------------------------------------------------------------------


def test_parse_envelope_valid() -> None:
    payload = json.dumps(
        {
            "specversion": "1.0",
            "type": "com.sap.integration.incident.detected.v1",
            "source": "/sap/s4hana",
            "id": "test-001",
            "data": {"incidentId": "INC-001", "priority": "HIGH", "description": "Test"},
        }
    ).encode()
    envelope = _parse_envelope(payload)
    assert envelope is not None
    assert envelope.id == "test-001"


def test_parse_envelope_invalid_json() -> None:
    result = _parse_envelope(b"not-json{{{")
    assert result is None


def test_parse_envelope_missing_required_fields() -> None:
    result = _parse_envelope(json.dumps({"foo": "bar"}).encode())
    assert result is None


# ---------------------------------------------------------------------------
# AmqpConsumerTask.start — desabilitado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amqp_consumer_task_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Quando AMQP_ENABLED=false a task não é criada."""
    monkeypatch.setattr("app.events.amqp_consumer.settings.amqp_enabled", False)

    consumer = AmqpConsumerTask()
    await consumer.start()

    assert consumer._task is None


# ---------------------------------------------------------------------------
# AmqpConsumerTask.start — habilitado (loop mockado)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amqp_consumer_task_starts_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifica que a task é criada e encerrada graciosamente."""
    monkeypatch.setattr("app.events.amqp_consumer.settings.amqp_enabled", True)
    monkeypatch.setattr("app.events.amqp_consumer.settings.amqp_host", "localhost")
    monkeypatch.setattr("app.events.amqp_consumer.settings.amqp_port", 5672)
    monkeypatch.setattr("app.events.amqp_consumer.settings.amqp_username", "guest")
    monkeypatch.setattr("app.events.amqp_consumer.settings.amqp_password", "guest")
    monkeypatch.setattr("app.events.amqp_consumer.settings.amqp_queue", "test-queue")
    monkeypatch.setattr("app.events.amqp_consumer.settings.amqp_reconnect_delay", 1)

    async def _mock_loop(stop_event: asyncio.Event) -> None:
        await stop_event.wait()

    consumer = AmqpConsumerTask()

    with patch("app.events.amqp_consumer._consume_loop", _mock_loop):
        await consumer.start()
        assert consumer._task is not None
        assert not consumer._task.done()

        await consumer.stop()
        assert consumer._task.done()


# ---------------------------------------------------------------------------
# _process_message — ack em sucesso
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_message_ack_on_success() -> None:
    """Mensagem válida deve chamar handle_incident_event e fazer ack."""
    from app.events.amqp_consumer import _process_message

    valid_payload = json.dumps(
        {
            "specversion": "1.0",
            "type": "com.sap.integration.incident.detected.v1",
            "source": "/sap/s4hana",
            "id": "ack-test-001",
            "data": {"incidentId": "INC-ACK", "priority": "MEDIUM", "description": "Ack test"},
        }
    ).encode()

    channel = MagicMock()
    channel.basic_ack = AsyncMock()
    channel.basic_reject = AsyncMock()
    channel.basic_nack = AsyncMock()

    delivery = MagicMock()
    delivery.delivery_tag = 42

    message = MagicMock()
    message.body = valid_payload
    message.channel = channel
    message.delivery = delivery

    with patch("app.events.amqp_consumer.handle_incident_event") as mock_handler:
        mock_handler.return_value = None
        await _process_message(message)

    channel.basic_ack.assert_called_once_with(delivery_tag=42)
    channel.basic_reject.assert_not_called()
    channel.basic_nack.assert_not_called()


# ---------------------------------------------------------------------------
# _process_message — reject em payload inválido
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_message_reject_on_invalid_payload() -> None:
    """Payload inválido deve fazer basic_reject sem requeue."""
    from app.events.amqp_consumer import _process_message

    channel = MagicMock()
    channel.basic_ack = AsyncMock()
    channel.basic_reject = AsyncMock()
    channel.basic_nack = AsyncMock()

    delivery = MagicMock()
    delivery.delivery_tag = 99

    message = MagicMock()
    message.body = b"not-valid-json"
    message.channel = channel
    message.delivery = delivery

    await _process_message(message)

    channel.basic_reject.assert_called_once_with(delivery_tag=99, requeue=False)
    channel.basic_ack.assert_not_called()


# ---------------------------------------------------------------------------
# _process_message — nack com requeue em erro de processamento
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_message_nack_on_handler_error() -> None:
    """Erro no handler deve fazer basic_nack com requeue=True."""
    from app.events.amqp_consumer import _process_message

    valid_payload = json.dumps(
        {
            "specversion": "1.0",
            "type": "com.sap.integration.incident.detected.v1",
            "source": "/sap/s4hana",
            "id": "nack-test-001",
            "data": {"incidentId": "INC-ERR", "priority": "LOW", "description": "Error test"},
        }
    ).encode()

    channel = MagicMock()
    channel.basic_ack = AsyncMock()
    channel.basic_reject = AsyncMock()
    channel.basic_nack = AsyncMock()

    delivery = MagicMock()
    delivery.delivery_tag = 77

    message = MagicMock()
    message.body = valid_payload
    message.channel = channel
    message.delivery = delivery

    with patch("app.events.amqp_consumer.handle_incident_event", side_effect=RuntimeError("boom")):
        await _process_message(message)

    channel.basic_nack.assert_called_once_with(delivery_tag=77, requeue=True)
    channel.basic_ack.assert_not_called()
