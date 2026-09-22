"""Consumidor de eventos de incidente (DA-23).

Ate esta fase, o Copilot so reagia a chamadas EXPLICITAS (POST
/diagnose humano, mensagem A2A, tool call MCP). Este modulo fecha o
sexto item do roadmap arquitetural: ingestao orientada a evento - um
sistema de monitoracao externo (CPI, Solution Manager, um listener de
IDoc/fila) publica um evento de "incidente detectado" e o Copilot
diagnostica sozinho, sem intervencao humana no meio.

Transporte escolhido: webhook HTTP (`POST /events/incident` em
app/main.py), nao um consumidor AMQP de fio. Isso NAO e um atalho para
evitar montar um broker real - webhook (REST/Webhook push
subscription) e um modo de entrega de primeira classe do proprio SAP
Event Mesh, documentado ao lado do AMQP, e e o unico que este projeto
consegue exercitar de ponta a ponta com testes reais (TestClient HTTP)
sem depender de infraestrutura externa - mesma logica pragmatica ja
aplicada ao GraphRAG (DA-21) e ao MCP (DA-19): codigo real, testado,
sem fingir uma validacao contra infraestrutura que nao esta disponivel
neste ambiente.
"""

from __future__ import annotations

import logging

from app.agent.graph import run_diagnosis
from app.models import DiagnosisResponse, IncidentEventEnvelope, IncidentRequest

_logger = logging.getLogger(__name__)


def to_incident_request(envelope: IncidentEventEnvelope) -> IncidentRequest:
    """O `data` do evento tem exatamente os mesmos campos de
    IncidentRequest (por design, ver app/models.py) - a conversao e so
    um remapeamento de schema, sem logica de negocio, para deixar
    explicito o ponto de fronteira entre "evento externo" e "requisicao
    interna que run_diagnosis() entende"."""
    return IncidentRequest(**envelope.data.model_dump())


def handle_incident_event(envelope: IncidentEventEnvelope) -> DiagnosisResponse:
    """Disparo automatico do diagnostico a partir de um evento -
    mesma orquestracao (`run_diagnosis`) usada por /diagnose e pela
    camada A2A, sem nenhuma logica duplicada.

    DA-23: os campos informativos do envelope CloudEvents (source, id, time)
    sao logados para rastreabilidade/correlacao antes de iniciar o diagnostico.
    Nao alternam a logica de negocio - sao observabilidade pura.
    """
    _logger.info(
        "[events] Evento de incidente recebido — "
        "cloudevents.source=%s cloudevents.id=%s cloudevents.time=%s",
        getattr(envelope, "source", None),
        getattr(envelope, "id", None),
        getattr(envelope, "time", None),
    )
    return run_diagnosis(to_incident_request(envelope))
