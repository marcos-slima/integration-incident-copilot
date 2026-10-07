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
   app/events/idempotency.py (P1.1: Redis Set compartilhado entre pods,
   fallback LRU em memoria) descarta reenvios; se o diagnostico falhar
   o id e liberado, para a reentrega/reprocessamento ser aceito.

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
from typing import TYPE_CHECKING

from app.agent.graph import run_diagnosis
from app.events import idempotency
from app.models import DiagnosisResponse, IncidentEventEnvelope, IncidentRequest

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

_logger = logging.getLogger(__name__)


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
        idempotency.mark_completed(idempotency.event_key(envelope))
        _logger.info(
            "[events] Diagnostico concluido em background — "
            "cloudevents.id=%s diagnosis_confidence=%.2f provider=%s",
            event_id,
            result.diagnosis_confidence,
            result.llm_provider_used,
        )
    except Exception:
        # Libera o id: sem isso a reentrega/reprocessamento do mesmo
        # cloudevents.id seria descartado como duplicata.
        idempotency.release(idempotency.event_key(envelope))
        # DLQ: log estruturado com todos os campos para reprocessamento
        # manual. Nao e silencioso - nivel ERROR garante que o operador
        # veja (alertas de log tipicamente filtram por nivel >= ERROR).
        _logger.exception(
            "[events] Falha no diagnostico em background (DLQ) — "
            "cloudevents.source=%s cloudevents.id=%s cloudevents.time=%s "
            "description=%r",
            getattr(envelope, "source", None),
            event_id,
            getattr(envelope, "time", None),
            getattr(envelope.data, "description", None),
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
) -> dict[str, str | None]:
    """§3.4 — Variante assincrona (202 Accepted) usada pelo endpoint
    POST /events/incident em app/main.py.

    B-02: com REDIS_URL, o evento e enfileirado no RQ (durable, com retry
    e FailedJobRegistry como DLQ) ANTES do 202 - se o enqueue falhar, a
    excecao propaga e o endpoint responde 503 para o publicador reenviar.
    A deduplicacao acontece no worker (app/queue.py::run_event_job).

    Sem REDIS_URL (desenvolvimento local): BackgroundTasks no proprio
    processo web, com dedup em memoria - NAO e durable (perde o evento
    em restart), ver warning de startup em app/events/idempotency.py."""
    from app.config import settings

    event_id = getattr(envelope, "id", None)
    _logger.info(
        "[events] Evento de incidente recebido — "
        "cloudevents.source=%s cloudevents.id=%s cloudevents.time=%s",
        getattr(envelope, "source", None),
        event_id,
        getattr(envelope, "time", None),
    )
    if settings.redis_url:
        from app.queue import enqueue_incident_event

        job_id = enqueue_incident_event(envelope.model_dump(mode="json"))
        return {"status": "queued", "job_id": job_id}

    if idempotency.is_duplicate(idempotency.event_key(envelope)):
        _logger.warning(
            "[events] Evento duplicado descartado (idempotencia) — cloudevents.id=%s",
            event_id,
        )
        return {"status": "duplicate", "job_id": None}
    background_tasks.add_task(_run_diagnosis_background, envelope)
    return {"status": "accepted", "job_id": None}
