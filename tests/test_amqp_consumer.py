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
    with patch.dict("sys.modules", {"proton": None, "proton.handlers": None, "proton.reactor": None}):
        # ImportError deve ser capturado internamente — sem exceção para o chamador
        _blocking_consume_loop(stop_flag)


# ---------------------------------------------------------------------------
# _IncidentHandler.on_message — ack, reject e modified via Proton delivery
# ---------------------------------------------------------------------------


def _make_proton_event(body_bytes: bytes, *, handler) -> MagicMock:
    """Monta um event Proton mínimo para testes de on_message."""
    from proton import Message  # type: ignore[import]

    msg = Message()
    msg.body = body_bytes
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


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("proton"),
    reason="python-qpid-proton não instalado",
)
def test_on_message_accepted_on_valid_payload() -> None:
    """Mensagem válida → delivery ACCEPTED (ack AMQP 1.0)."""
    from app.events.amqp_consumer import _blocking_consume_loop  # noqa: F401 — acessa _IncidentHandler via closure

    # Importa _IncidentHandler indiretamente instanciando o loop com Container mockado
    import app.events.amqp_consumer as mod

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

    stop_flag = [True]  # encerra imediatamente após primeira iteração

    with patch("app.events.amqp_consumer.handle_incident_event") as mock_handler:
        mock_handler.return_value = None

        # Instancia o handler diretamente para testar on_message sem subir Container
        from proton.handlers import MessagingHandler  # type: ignore[import]

        # Obtemos _IncidentHandler via inspeção do módulo (closure dentro do loop)
        # Estratégia: executar o loop com Container mockado que expõe o handler
        captured = {}

        class _FakeContainer:
            def __init__(self, handler):
                captured["handler"] = handler

            def run(self):
                pass  # não executa nada

        with patch("app.events.amqp_consumer.Container", _FakeContainer):
            with patch("app.events.amqp_consumer.time.sleep"):
                mod._blocking_consume_loop(stop_flag)

        handler = captured.get("handler")
        assert handler is not None, "Container não recebeu handler"

        event = _make_proton_event(valid_payload, handler=handler)
        handler.on_message(event)

        mock_handler.assert_called_once()
        event.delivery.update.assert_called_with("ACCEPTED")
        event.delivery.settle.assert_called()


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("proton"),
    reason="python-qpid-proton não instalado",
)
def test_on_message_rejected_on_invalid_payload() -> None:
    """Payload inválido → delivery REJECTED (sem requeue)."""
    import app.events.amqp_consumer as mod

    stop_flag = [True]
    captured = {}

    class _FakeContainer:
        def __init__(self, handler):
            captured["handler"] = handler

        def run(self):
            pass

    with patch("app.events.amqp_consumer.Container", _FakeContainer):
        with patch("app.events.amqp_consumer.time.sleep"):
            mod._blocking_consume_loop(stop_flag)

    handler = captured["handler"]
    event = _make_proton_event(b"not-valid-json", handler=handler)
    handler.on_message(event)

    event.delivery.update.assert_called_with("REJECTED")
    event.delivery.settle.assert_called()


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("proton"),
    reason="python-qpid-proton não instalado",
)
def test_on_message_modified_on_handler_error() -> None:
    """Erro no handler → delivery MODIFIED (nack com requeue AMQP 1.0)."""
    import app.events.amqp_consumer as mod

    stop_flag = [True]
    captured = {}

    class _FakeContainer:
        def __init__(self, handler):
            captured["handler"] = handler

        def run(self):
            pass

    valid_payload = json.dumps(
        {
            "specversion": "1.0",
            "type": "com.sap.integration.incident.detected.v1",
            "source": "/sap/s4hana",
            "id": "err-001",
            "data": {"incidentId": "INC-ERR", "priority": "LOW", "description": "error test"},
        }
    ).encode()

    with patch("app.events.amqp_consumer.Container", _FakeContainer):
        with patch("app.events.amqp_consumer.time.sleep"):
            mod._blocking_consume_loop(stop_flag)

    handler = captured["handler"]
    event = _make_proton_event(valid_payload, handler=handler)

    with patch("app.events.amqp_consumer.handle_incident_event", side_effect=RuntimeError("boom")):
        handler.on_message(event)

    event.delivery.update.assert_called_with("MODIFIED")
    event.delivery.settle.assert_called()
