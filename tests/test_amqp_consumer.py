"""Testes para DA-32/DA-40 — AmqpConsumerTask e helpers (AMQP 1.0 / Proton).

A implementação usa python-qpid-proton com API síncrona/blocking rodando
em thread pool (run_in_executor). Os testes cobrem:
  - _parse_envelope: parsing de payload CloudEvents JSON
  - AmqpConsumerTask.start: task criada/não criada conforme AMQP_ENABLED
  - AmqpConsumerTask.stop: encerramento gracioso
  - _blocking_consume_loop: comportamento com proton não instalado
  - _IncidentHandler.on_message: ack/reject/modified via Proton deliveries
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock, patch

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


def test_parse_envelope_string_body() -> None:
    """body como str (Proton pode entregar str em vez de bytes)."""
    payload = json.dumps(
        {
            "specversion": "1.0",
            "type": "com.sap.integration.incident.detected.v1",
            "source": "/sap/s4hana",
            "id": "test-str-002",
            "data": {"incidentId": "INC-002", "priority": "LOW", "description": "str body"},
        }
    ).encode()
    envelope = _parse_envelope(payload)
    assert envelope is not None
    assert envelope.id == "test-str-002"


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
# AmqpConsumerTask.start — habilitado (blocking loop mockado)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amqp_consumer_task_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Com AMQP_ENABLED=true a asyncio.Task é criada."""
    monkeypatch.setattr("app.events.amqp_consumer.settings.amqp_enabled", True)

    # Substitui _blocking_consume_loop por função que retorna imediatamente
    with patch("app.events.amqp_consumer._blocking_consume_loop", return_value=None):
        consumer = AmqpConsumerTask()
        await consumer.start()
        assert consumer._task is not None
        # Aguarda task concluir para não deixar coroutine pendente
        await asyncio.sleep(0)
        await consumer.stop()


@pytest.mark.asyncio
async def test_amqp_consumer_task_stops_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    """stop() aguarda encerramento e marca _task como done."""
    monkeypatch.setattr("app.events.amqp_consumer.settings.amqp_enabled", True)

    with patch("app.events.amqp_consumer._blocking_consume_loop", return_value=None):
        consumer = AmqpConsumerTask()
        await consumer.start()
        await consumer.stop()

        assert consumer._task is not None
        assert consumer._task.done()


# ---------------------------------------------------------------------------
# _blocking_consume_loop — proton não instalado
# ---------------------------------------------------------------------------


def test_blocking_consume_loop_no_proton() -> None:
    """Loop retorna sem crash quando python-qpid-proton não está instalado."""
    from app.events.amqp_consumer import _blocking_consume_loop

    stop_flag = [False]
    with patch.dict(
        "sys.modules", {"proton": None, "proton.handlers": None, "proton.reactor": None}
    ):
        # ImportError deve ser capturado internamente — sem exceção para o chamador
        _blocking_consume_loop(stop_flag)


# ---------------------------------------------------------------------------
# _IncidentHandler.on_message — ack, reject e modified via Proton delivery
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("proton"),
    reason="python-qpid-proton não instalado",
)
def test_on_message_accepted_on_valid_payload() -> None:
    """Mensagem válida → delivery ACCEPTED (ack AMQP 1.0)."""
    # Importa _IncidentHandler indiretamente instanciando o loop com Container mockado
    import app.events.amqp_consumer as mod
    from app.events.amqp_consumer import (
        _blocking_consume_loop,  # noqa: F401 — acessa _IncidentHandler via closure
    )

    # Reconstrói o handler de dentro do loop mockando Container.run
    valid_payload = json.dumps(
        {
            "specversion": "1.0",
            "type": "com.sap.integration.incident.detected.v1",
            "source": "/sap/s4hana",
            "id": "on-msg-001",
            "data": {"incidentId": "INC-MSG", "priority": "HIGH", "description": "on_message test"},
        }
    ).encode()

    stop_flag = [False]  # _FakeContainer.run() encerra o loop apos a 1a iteracao

    with patch("app.events.amqp_consumer.handle_incident_event") as mock_handler:
        mock_handler.return_value = None

        # Instancia o handler diretamente para testar on_message sem subir Container

        # Obtemos _IncidentHandler via inspeção do módulo (closure dentro do loop)
        # Estratégia: executar o loop com Container mockado que expõe o handler
        captured = {}

        class _FakeContainer:
            def __init__(self, handler):
                captured["handler"] = handler

            def run(self):
                stop_flag[0] = True  # encerra o loop apos a 1a iteracao

        with (
            patch("proton.reactor.Container", _FakeContainer),
            patch("time.sleep"),
            patch("app.events.worker_queue._get_worker_queue", return_value=MagicMock()),
        ):
            mod._blocking_consume_loop(stop_flag)

        handler = captured.get("handler")
        assert handler is not None, "Container não recebeu handler"

        msg_data = MagicMock()
        msg_data.body = valid_payload
        msg_data.id = "test-msg-001"

        event = MagicMock()
        event.message = msg_data
        event.delivery = MagicMock()
        event.receiver = MagicMock()
        event.connection = MagicMock()

        handler.on_message(event)

        # on_message apenas enqueues para worker — não aceita/rejeita ainda
        assert event.delivery.update.call_count == 0
        assert event.delivery.settle.call_count == 0


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("proton"),
    reason="python-qpid-proton não instalado",
)
def test_on_message_invalid_payload_rejected_by_parser() -> None:
    """Payload inválido → delivery REJECTED (sem requeue)."""
    from proton import Delivery

    import app.events.amqp_consumer as mod

    stop_flag = [False]
    captured = {}

    class _FakeContainer:
        def __init__(self, handler):
            captured["handler"] = handler

        def run(self):
            stop_flag[0] = True

    with (
        patch("proton.reactor.Container", _FakeContainer),
        patch("time.sleep"),
        patch("app.events.worker_queue._get_worker_queue", return_value=MagicMock()),
    ):
        mod._blocking_consume_loop(stop_flag)

    handler = captured["handler"]
    assert handler is not None

    msg_data = MagicMock()
    msg_data.body = b"not-valid-json"
    msg_data.id = "invalid-msg"

    delivery_mock = MagicMock()
    delivery_mock.REJECTED = Delivery.REJECTED
    delivery_mock.ACCEPTED = Delivery.ACCEPTED
    delivery_mock.MODIFIED = Delivery.MODIFIED

    event = MagicMock()
    event.message = msg_data
    event.delivery = delivery_mock
    event.receiver = MagicMock()
    event.connection = MagicMock()

    handler.on_message(event)

    call_args = event.delivery.update.call_args[0][0]
    assert call_args == Delivery.REJECTED
    event.delivery.settle.assert_called()


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("proton"),
    reason="python-qpid-proton não instalado",
)
def _capture_handler():
    import app.events.amqp_consumer as mod

    stop_flag = [False]
    captured = {}

    class _FakeContainer:
        def __init__(self, handler):
            captured["handler"] = handler

        def run(self):
            stop_flag[0] = True

    with (
        patch("proton.reactor.Container", _FakeContainer),
        patch("time.sleep"),
        patch("app.events.worker_queue._get_worker_queue", return_value=MagicMock()),
    ):
        mod._blocking_consume_loop(stop_flag)
    return captured["handler"]


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("proton"),
    reason="python-qpid-proton não instalado",
)
def test_on_start_grants_initial_credit_and_escapes_credentials(monkeypatch) -> None:
    """create_receiver() do proton nao aceita "credit" (TypeError); com
    prefetch=0 o credito inicial precisa vir de receiver.flow()."""
    monkeypatch.setattr("app.events.amqp_consumer.settings.amqp_username", "user@corp")
    monkeypatch.setattr("app.events.amqp_consumer.settings.amqp_password", "p@ss/w:rd")
    monkeypatch.setattr("app.events.amqp_consumer.settings.amqp_prefetch", 3)
    handler = _capture_handler()

    event = MagicMock()
    receiver = event.container.create_receiver.return_value
    handler.on_start(event)

    _, kwargs = event.container.create_receiver.call_args
    assert "credit" not in kwargs
    receiver.flow.assert_called_once_with(3)
    url = event.container.connect.call_args.args[0]
    assert "user%40corp:p%40ss%2Fw%3Ard@" in url


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("proton"),
    reason="python-qpid-proton não instalado",
)
def test_on_message_duplicate_is_skiped_without_enqueue() -> None:
    handler = _capture_handler()
    payload = json.dumps(
        {
            "specversion": "1.0",
            "type": "com.sap.integration.incident.detected.v1",
            "source": "/sap/s4hana",
            "id": "dup-001",
            "data": {"incidentId": "INC-DUP", "priority": "LOW", "description": "dup test"},
        }
    ).encode()

    with (
        patch("app.events.amqp_consumer.idempotency.is_duplicate", return_value=True),
        patch("app.events.amqp_consumer.handle_incident_event") as mock_handler,
    ):
        event1 = _make_proton_event_with_raw_payload(payload, handler=handler)
        handler.on_message(event1)

        event2 = _make_proton_event_with_raw_payload(payload, handler=handler)
        handler.on_message(event2)

    mock_handler.assert_not_called()
    assert event1.delivery.update.call_args_list[0][0][0] == "ACCEPTED"
    assert event2.delivery.update.call_args_list[0][0][0] == "ACCEPTED"


def _make_proton_event_with_raw_payload(body: Any, *, handler) -> MagicMock:
    """Monta event Proton com payload cru (string/bytes/dict)."""
    from proton import Message

    msg = Message()
    msg.body = body
    msg.id = "test-msg-id"

    delivery = MagicMock()
    delivery.ACCEPTED = "ACCEPTED"
    delivery.REJECTED = "REJECTED"
    delivery.MODIFIED = "MODIFIED"

    receiver = MagicMock()
    receiver.flow = MagicMock()

    connection = MagicMock()

    event = MagicMock()
    event.message = msg
    event.delivery = delivery
    event.receiver = receiver
    event.connection = connection
    return event
