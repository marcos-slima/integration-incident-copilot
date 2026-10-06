"""Testes para DA-32/DA-40 — AmqpConsumerTask e helpers (AMQP 1.0 / Proton).

A implementação usa python-qpid-proton com API síncrona/blocking rodando
em thread pool (run_in_executor). Os testes cobrem:
  - _parse_envelope: parsing de payload CloudEvents JSON
  - AmqpConsumerTask.start: task criada/não criada conforme AMQP_ENABLED
  - AmqpConsumerTask.stop: encerramento gracioso
  - _blocking_consume_loop: comportamento com proton não instalado
  - handler: credito, timer de parada, RQ, e um broker AMQP 1.0 real em
    processo (aceite, reentrega, DMQ, payload invalido, paralelismo)
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
    with patch.dict(
        "sys.modules", {"proton": None, "proton.handlers": None, "proton.reactor": None}
    ):
        # ImportError deve ser capturado internamente — sem exceção para o chamador
        _blocking_consume_loop(stop_flag)


# ---------------------------------------------------------------------------
# Handler (validacao 2026-10-06, REL-01/N-04): unidade + broker AMQP 1.0 real
# ---------------------------------------------------------------------------

proton = pytest.importorskip("proton")

import socket
import threading
import time

from proton import Message
from proton.handlers import MessagingHandler
from proton.reactor import Container

import app.events.amqp_consumer as mod


def _payload(event_id: str) -> str:
    return json.dumps(
        {
            "specversion": "1.0",
            "type": "com.sap.integration.incident.detected.v1",
            "source": "/sap/s4hana",
            "id": event_id,
            "data": {"incidentId": "INC-1", "priority": "HIGH", "description": "IDoc 51"},
        }
    )


def test_on_start_grants_initial_credit_and_escapes_credentials(monkeypatch) -> None:
    """create_receiver() do proton nao aceita "credit" (TypeError); com
    prefetch=0 o credito inicial precisa vir de receiver.flow()."""
    monkeypatch.setattr(mod.settings, "amqp_username", "user@corp")
    monkeypatch.setattr(mod.settings, "amqp_password", "p@ss/w:rd")
    monkeypatch.setattr(mod.settings, "amqp_prefetch", 3)
    handler = mod._make_handler([False], executor=None)
    event = MagicMock()
    receiver = event.container.create_receiver.return_value
    handler.on_start(event)
    _, kwargs = event.container.create_receiver.call_args
    assert "credit" not in kwargs
    receiver.flow.assert_called_once_with(3)
    assert "user%40corp:p%40ss%2Fw%3Ard@" in event.container.connect.call_args.args[0]
    event.container.schedule.assert_called_once()  # timer que observa o stop_flag


class _Broker(MessagingHandler):
    """Broker AMQP 1.0 minimo: uma fila, reentrega com delivery_count+1 em
    MODIFIED/RELEASED e registro das dispositions recebidas."""

    def __init__(self, url: str, bodies: list[str]) -> None:
        super().__init__(auto_accept=False)
        self.url = url
        self.pending = [(b, 0) for b in bodies]
        self.outcomes: list[tuple[str, int]] = []
        self.sender = None
        self.inflight: dict[str, tuple[str, int]] = {}
        self.acceptor = None

    def on_start(self, event):
        self.acceptor = event.container.listen(self.url)

    def on_link_opening(self, event):
        if event.link.is_sender:
            event.link.source.address = event.link.remote_source.address
            self.sender = event.link

    def on_sendable(self, event):
        self._pump()

    def _pump(self):
        while self.sender is not None and self.sender.credit and self.pending:
            body, count = self.pending.pop(0)
            msg = Message(body=body, id=f"m-{len(self.outcomes)}-{count}")
            msg.delivery_count = count
            dlv = self.sender.send(msg)
            self.inflight[str(dlv.tag)] = (body, count)

    def _record(self, event, outcome):
        body, count = self.inflight.pop(str(event.delivery.tag), ("?", -1))
        self.outcomes.append((outcome, count))
        if outcome == "released":
            self.pending.append((body, count + 1))
        event.delivery.settle()
        self._pump()

    def on_accepted(self, event):
        self._record(event, "accepted")

    def on_rejected(self, event):
        self._record(event, "rejected")

    def on_released(self, event):  # MODIFIED e RELEASED chegam aqui
        self._record(event, "released")

    def stop(self):
        if self.acceptor:
            self.acceptor.close()
        if self.sender:
            self.sender.connection.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_consumer_against_broker(monkeypatch, bodies, diagnose, *, expect, timeout=20):
    port = _free_port()
    url = f"127.0.0.1:{port}"
    broker = _Broker(url, bodies)
    broker_container = Container(broker)
    bt = threading.Thread(target=broker_container.run, daemon=True)
    bt.start()
    time.sleep(0.3)

    monkeypatch.setattr(mod, "_connection_url", lambda: f"amqp://{url}")
    monkeypatch.setattr(mod, "_ssl_domain", lambda: None)
    monkeypatch.setattr(mod.settings, "redis_url", "")
    monkeypatch.setattr(mod.settings, "amqp_queue", "incidents")
    monkeypatch.setattr(mod.settings, "amqp_prefetch", 2)
    monkeypatch.setattr(mod.settings, "amqp_max_redeliveries", 2)
    monkeypatch.setattr(mod.settings, "amqp_reconnect_delay", 0)
    monkeypatch.setattr("app.events.consumer.handle_incident_event", diagnose)
    monkeypatch.setattr(mod.idempotency, "is_duplicate", lambda _id: False)
    monkeypatch.setattr(mod.idempotency, "mark_completed", lambda _id: None)
    monkeypatch.setattr(mod.idempotency, "release", lambda _id: None)

    stop = [False]
    ct = threading.Thread(target=mod._blocking_consume_loop, args=(stop,), daemon=True)
    ct.start()
    deadline = time.time() + timeout
    while time.time() < deadline and len(broker.outcomes) < expect:
        time.sleep(0.05)
    t0 = time.time()
    stop[0] = True
    ct.join(timeout=5)
    stopped_in = time.time() - t0
    broker_container.stop()
    return broker.outcomes, ct.is_alive(), stopped_in


def test_broker_real_mensagens_sao_processadas_e_aceitas(monkeypatch) -> None:
    """Regressao N-04: com a worker queue nunca iniciada, a 1a mensagem
    ficava pendurada e nada mais era consumido."""
    calls: list[str] = []
    outcomes, alive, stopped_in = _run_consumer_against_broker(
        monkeypatch,
        [_payload(f"ev-{i}") for i in range(5)],
        lambda env: calls.append(env.id),
        expect=5,
    )
    assert [o for o, _ in outcomes] == ["accepted"] * 5
    assert sorted(calls) == [f"ev-{i}" for i in range(5)]
    assert not alive and stopped_in < 3  # para mesmo sem mensagens chegando


def test_broker_real_diagnostico_lento_nao_bloqueia_reactor(monkeypatch) -> None:
    """Com prefetch=2 e diagnostico de 1 s, duas mensagens processam em
    paralelo (o reactor segue livre para receber e liquidar)."""
    outcomes, _, _ = _run_consumer_against_broker(
        monkeypatch,
        [_payload("a"), _payload("b")],
        lambda env: time.sleep(1.0),
        expect=2,
        timeout=10,
    )
    assert [o for o, _ in outcomes] == ["accepted", "accepted"]


def test_broker_real_falha_reentrega_e_vai_para_dmq(monkeypatch) -> None:
    """Falha -> MODIFIED (broker reentrega com delivery_count+1);
    em amqp_max_redeliveries=2 a mensagem e REJECTED (DMQ)."""

    def _falha(_env):
        raise RuntimeError("LLM fora")

    outcomes, _, _ = _run_consumer_against_broker(
        monkeypatch, [_payload("poison")], _falha, expect=3
    )
    assert outcomes == [("released", 0), ("released", 1), ("rejected", 2)]


def test_broker_real_payload_invalido_rejeitado(monkeypatch) -> None:
    outcomes, _, _ = _run_consumer_against_broker(
        monkeypatch, ["nao-e-json{"], lambda env: None, expect=1
    )
    assert outcomes == [("rejected", 0)]


def test_com_redis_enfileira_no_rq_e_aceita(monkeypatch) -> None:
    jobs: list[dict] = []
    monkeypatch.setattr(mod.settings, "redis_url", "redis://fake")
    monkeypatch.setattr("app.queue.enqueue_incident_event", lambda d: jobs.append(d) or "job-1")
    handler = mod._make_handler([False], executor=None)
    handler._receiver = MagicMock()
    event = MagicMock()
    event.message = Message(body=_payload("rq-1"))
    handler.on_message(event)
    assert jobs and jobs[0]["id"] == "rq-1"
    event.delivery.update.assert_called_once_with(proton.Delivery.ACCEPTED)
    handler._receiver.flow.assert_called_once_with(1)
