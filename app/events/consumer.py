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

Avaliacao externa §3.4 — tres melhorias de resiliencia adicionadas:

1. 202 Accepted + BackgroundTasks: o endpoint em app/main.py passou a
   chamar `handle_incident_event_async`, que recebe um
   `BackgroundTasks` do FastAPI e retorna 202 imediatamente. O
   diagnostico roda em background - o publicador nao precisa esperar
   os segundos de inferencia do LLM para receber confirmacao de
   recebimento. Isso previne timeouts no publicador (SAP Event Mesh
   tem timeout padrao de ~30s para confirmacao de entrega).

2. Idempotencia por cloudevents.id: o SAP Event Mesh pode reenviar o
   mesmo evento em caso de timeout ou falha de rede (at-least-once
   delivery). Sem deduplicacao, o mesmo incidente seria diagnosticado
   duas vezes, gerando traces duplicados no Langfuse e logs confusos.
   `_SEEN_EVENT_IDS` (LRU in-memory, tamanho limitado) armazena os
   ids ja processados e descarta reenvios, logando um aviso.

3. DLQ (Dead Letter Queue) simples: excecoes durante o diagnostico em
   background nao mais silam silenciosamente. Sao logadas com nivel
   ERROR e os campos completos do envelope (source, id, time,
   description) para que um operador possa reprocessar manualmente.
   Nao-objetivo: nao ha fila de retry automatico (isso exigiria RQ/
   Redis - coberto em app/queue.py para /diagnose/async); o DLQ aqui
   e o log estruturado, suficiente para o volume esperado de eventos.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import TYPE_CHECKING

from app.agent.graph import run_diagnosis
from app.models import DiagnosisResponse, IncidentEventEnvelope, IncidentRequest

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

_logger = logging.getLogger(__name__)

# LRU de ids ja vistos — limita memoria a `_SEEN_MAX` entradas.
# Suficiente para absorver reenvios do SAP Event Mesh (que garante
# "at-least-once" com janela tipica de minutos, nao dias).
# In-memory: perdido no restart, mas um restart tambem reseta o
# pipeline de diagnostico — reprocessar eventos recentes e aceitavel.
_SEEN_MAX = 2_000
_SEEN_EVENT_IDS: OrderedDict[str, bool] = OrderedDict()


def _is_duplicate(event_id: str | None) -> bool:
    """Retorna True se `event_id` ja foi processado (deduplicacao).
    None (evento sem id) nunca e deduplicado - melhor processar duas
    vezes do que descartar um evento valido sem id."""
    if not event_id:
        return False
    if event_id in _SEEN_EVENT_IDS:
        return True
    # Registra o novo id; evicao FIFO quando o limite e atingido.
    _SEEN_EVENT_IDS[event_id] = True
    if len(_SEEN_EVENT_IDS) > _SEEN_MAX:
        _SEEN_EVENT_IDS.popitem(last=False)
    return False


def to_incident_request(envelope: IncidentEventEnvelope) -> IncidentRequest:
    """O `data` do evento tem exatamente os mesmos campos de
    IncidentRequest (por design, ver app/models.py) - a conversao e so
    um remapeamento de schema, sem logica de negocio, para deixar
    explicito o ponto de fronteira entre "evento externo" e "requisicao
    interna que run_diagnosis() entende"."""
    return IncidentRequest(**envelope.data.model_dump())


def _run_diagnosis_background(envelope: IncidentEventEnvelope) -> None:
    """Roda o diagnostico em background e trata excecoes como DLQ de log.
    Chamado por BackgroundTasks do FastAPI - qualquer excecao aqui nao
    chega ao caller (FastAPI silencia excecoes em background tasks por
    design) a menos que seja logada explicitamente."""
    event_id = getattr(envelope, "id", None)
    try:
        result = run_diagnosis(to_incident_request(envelope))
        _logger.info(
            "[events] Diagnostico concluido em background — "
            "cloudevents.id=%s confidence=%.2f provider=%s",
            event_id,
            result.confidence,
            result.llm_provider_used,
        )
    except Exception:  # noqa: BLE001
        # DLQ: log estruturado com todos os campos para reprocessamento
        # manual. Nao e silencioso - nivel ERROR garante que o operador
        # veja (alertas de log tipicamente filtram por nivel >= ERROR).
        _logger.error(
            "[events] Falha no diagnostico em background (DLQ) — "
            "cloudevents.source=%s cloudevents.id=%s cloudevents.time=%s "
            "description=%r",
            getattr(envelope, "source", None),
            event_id,
            getattr(envelope, "time", None),
            getattr(envelope.data, "description", None),
            exc_info=True,
        )


def handle_incident_event(envelope: IncidentEventEnvelope) -> DiagnosisResponse:
    """Compatibilidade retroativa — chama run_diagnosis() sincronamente.
    Mantido para testes que testam o comportamento sincrono (DA-23) e
    para o AMQP consumer (app/events/amqp_consumer.py), que controla
    seu proprio loop de eventos e nao usa BackgroundTasks do FastAPI.

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


def handle_incident_event_async(
    envelope: IncidentEventEnvelope,
    background_tasks: BackgroundTasks,
) -> None:
    """§3.4 — Variante assincrona (202 Accepted): registra o diagnostico
    como BackgroundTask do FastAPI e retorna imediatamente, sem esperar
    a inferencia do LLM. Usada pelo endpoint POST /events/incident em
    app/main.py (que agora responde 202, nao mais DiagnosisResponse).

    Deduplicacao por cloudevents.id antes de enfileirar: eventos ja
    processados sao descartados silenciosamente (log WARNING), evitando
    diagnosticos duplicados causados por reenvios do SAP Event Mesh."""
    event_id = getattr(envelope, "id", None)
    _logger.info(
        "[events] Evento de incidente recebido — "
        "cloudevents.source=%s cloudevents.id=%s cloudevents.time=%s",
        getattr(envelope, "source", None),
        event_id,
        getattr(envelope, "time", None),
    )
    if _is_duplicate(event_id):
        _logger.warning(
            "[events] Evento duplicado descartado (idempotencia) — " "cloudevents.id=%s",
            event_id,
        )
        return
    background_tasks.add_task(_run_diagnosis_background, envelope)
