"""Idempotencia distribuida para eventos de incidente (P1.1).

Antes desta fase, handle_incident_event() usava um set Python em memoria
(_SEEN_EVENT_IDS) para evitar reprocessar o mesmo cloudevents.id. Isso
funcionava para um unico pod/processo, mas falha silenciosamente em deploy
Kyma com replicas > 1: cada pod tem seu proprio set, entao o mesmo evento
pode ser processado duas vezes por pods diferentes.

P1.1 substitui o set em memoria por chaves Redis por evento
("events:seen:<id>", SET NX EX), compartilhadas entre todos os pods:

  - SET NX cria a chave se o evento e novo; retorna vazio se ja existia.
  - TTL de 24h por chave, gravado na mesma operacao atomica (eventos mais
    antigos que 24h raramente sao reentregues por qualquer broker).
  - Fallback: se Redis nao estiver disponivel (REDIS_URL vazio ou Redis
    fora do ar), usa um LRU em memoria — dedup so dentro do pod,
    preservando a disponibilidade em ambiente de desenvolvimento
    sem Redis. Log de warning explicito quando o fallback e acionado.

Integracao com P0.4 (consumer.py): cloudevents.id ja e usado como
job_id no RQ (P0.4). A verificacao de idempotencia aqui e uma segunda
linha de defesa em nivel de evento, antes do enqueue — complementar
ao RQ, que nao reprocessa um job com o mesmo ID, mas nao impede a
criacao de dois jobs com IDs distintos se o evento chegar com IDs
diferentes (cenario de reentrega com ID diferente).

Startup validation (P1.1): _warn_if_redis_missing_with_replicas() e
chamado no lifespan do FastAPI (app/main.py). Ela so emite um WARNING
(nao levanta excecao) se REDIS_URL estiver ausente, para preservar o
principio "clone e rode" sem infra obrigatoria. Operadores que querem
falha explicita devem combinar com REQUIRE_AUTH=true ou uma checagem
propria no processo de deploy.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict

_logger = logging.getLogger(__name__)

# Prefixo de namespace Redis — evita colisao com chaves do RQ ("rq:*")
# e do TaskStore A2A ("a2a:task:*"). Uma chave por evento
# ("events:seen:<id>"), gravada com SET NX EX: claim e TTL numa unica
# operacao atomica (um Set unico com SADD + EXPIRE separados podia ficar
# sem TTL se o EXPIRE falhasse, e o TTL valia para o set inteiro).
_REDIS_KEY_PREFIX = "events:seen:"

# Ciclo de vida de um cloudevents.id (B-03):
#   is_duplicate() -> "processing" com TTL = lease (claim)
#   mark_completed() -> "done" com TTL de 24h
#   release()        -> apaga (falha observada: reentrega aceita)
# Se o processo morrer entre o claim e a conclusao, o lease expira e a
# reentrega do broker volta a ser aceita - sem isso o evento ficaria
# bloqueado como "duplicado" por 24h.
#
# TTL de "done": 24h. Mesmo evento reentregue depois de 24h (raro, mas
# possivel em brokers com politica de retry longa) sera reprocessado —
# aceitavel dado que o objetivo e deduplicar entregas rapidas.
_IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60
_PROCESSING = "processing"
_DONE = "done"


def _processing_lease_seconds() -> int:
    """Lease do claim: cobre o watchdog do diagnostico com folga."""
    from app.config import settings

    return int(settings.diagnosis_timeout_seconds) + 120


# Fallback em memoria (LRU) quando Redis nao esta disponivel - mantem a
# deduplicacao dentro de um unico pod (comportamento pre-P1.1). Guarda
# o instante de expiracao de cada id; o lock torna check-then-set
# atomico entre threads (BackgroundTasks, threadpool do FastAPI).
_LOCAL_SEEN_MAX = 2_000
_local_seen: OrderedDict[str, float] = OrderedDict()
_local_lock = threading.Lock()


def _local_claim(event_id: str) -> bool:
    """Registra event_id no LRU local com lease. Retorna True se ja existia."""
    now = time.monotonic()
    with _local_lock:
        expires_at = _local_seen.get(event_id)
        if expires_at is not None and expires_at > now:
            return True
        _local_seen[event_id] = now + _processing_lease_seconds()
        _local_seen.move_to_end(event_id)
        if len(_local_seen) > _LOCAL_SEEN_MAX:
            _local_seen.popitem(last=False)
        return False


# Lazy: evita import de redis no startup quando REDIS_URL nao esta
# configurada (mesmo padrao de _load_queue() em consumer.py e
# _get_redis_client() em a2a/task_store.py)
_redis_client = None
# Instante (monotonic) ate o qual nao tentamos reconectar apos uma falha
# de conexao - antes a indisponibilidade era permanente ate o restart.
_redis_retry_after: float = 0.0
_REDIS_RETRY_SECONDS = 30.0


def _get_client():
    """Retorna o cliente Redis, criando-o na primeira chamada.
    Retorna None se Redis nao estiver configurado ou estiver indisponivel;
    apos uma falha, tenta reconectar a cada _REDIS_RETRY_SECONDS."""
    global _redis_client, _redis_retry_after

    if _redis_client is not None:
        return _redis_client

    from app.config import settings

    if not settings.redis_url or time.monotonic() < _redis_retry_after:
        return None

    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        # Ping para verificar conectividade antes de usar
        client.ping()
        _redis_client = client
        return _redis_client
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "[idempotency] Redis nao disponivel — idempotencia distribuida "
            "desabilitada por %ss (fallback: dedup so dentro do pod). erro=%s",
            int(_REDIS_RETRY_SECONDS),
            exc,
        )
        _redis_retry_after = time.monotonic() + _REDIS_RETRY_SECONDS
        return None


def is_duplicate(event_id: str | None) -> bool:
    """Verifica se o evento ja foi visto e, se nao, faz o claim dele.

    Retorna True se o evento ja foi concluido ou esta em processamento
    (lease ativo), False se e novo - nesse caso o caller passa a ser o
    dono do evento e DEVE chamar mark_completed() no sucesso ou
    release() na falha.

    Semantica atomica: SET NX EX cria a chave do evento com TTL numa unica
    operacao, sem race condition entre pods e sem chave orfa sem TTL.

    Evento sem id (None/"") nunca e deduplicado - melhor processar duas
    vezes do que descartar um evento valido. Sem Redis, usa o LRU em
    memoria (dedup apenas dentro do pod).
    """
    if not event_id:
        return False
    client = _get_client()
    if client is None:
        return _local_claim(event_id)

    try:
        # SET NX EX: True se a chave foi criada (evento novo), None se ja existia
        created = client.set(
            _REDIS_KEY_PREFIX + event_id, _PROCESSING, nx=True, ex=_processing_lease_seconds()
        )
        if not created:
            _logger.info(
                "[idempotency] Evento duplicado detectado — ignorando. cloudevents.id=%s",
                event_id,
            )
            return True
        return False
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "[idempotency] Erro ao verificar idempotencia no Redis — "
            "processando evento normalmente (fail-open). "
            "cloudevents.id=%s erro=%s",
            event_id,
            exc,
        )
        return False  # fail-open: prefere reprocessar a perder evento


def mark_completed(event_id: str | None) -> None:
    """Converte o claim em "done" por 24h - chamado apos o processamento
    do evento concluir com sucesso."""
    if not event_id:
        return
    with _local_lock:
        if event_id in _local_seen:
            _local_seen[event_id] = time.monotonic() + _IDEMPOTENCY_TTL_SECONDS
    client = _get_client()
    if client is None:
        return
    try:
        client.set(_REDIS_KEY_PREFIX + event_id, _DONE, ex=_IDEMPOTENCY_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "[idempotency] Falha ao marcar cloudevents.id=%s como concluido - "
            "uma reentrega apos o lease sera reprocessada. erro=%s",
            event_id,
            exc,
        )


def release(event_id: str | None) -> None:
    """Desfaz o claim de is_duplicate() - chamado quando o processamento
    do evento falhou, para que uma reentrega (Event Mesh, AMQP Modified,
    retry do RQ ou reprocessamento manual a partir do DLQ) seja aceita."""
    if not event_id:
        return
    with _local_lock:
        _local_seen.pop(event_id, None)
    client = _get_client()
    if client is None:
        return
    try:
        client.delete(_REDIS_KEY_PREFIX + event_id)
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "[idempotency] Falha ao liberar cloudevents.id=%s no Redis - "
            "reentrega sera descartada ate o lease expirar. erro=%s",
            event_id,
            exc,
        )


def _warn_if_redis_missing_with_replicas() -> None:
    """Emite WARNING de startup se REDIS_URL nao estiver configurada.

    P1.1: em deploy Kyma com replicas > 1, a ausencia de REDIS_URL
    significa que idempotencia distribuida e A2A TaskStore sao
    em-memoria — cada pod tem seu proprio estado, o que pode resultar
    em eventos duplicados processados por pods diferentes e GET /a2a/task
    retornando 404 quando a task foi criada em outro pod.

    Emite WARNING (nao excecao) para preservar "clone e rode" sem infra
    obrigatoria. Operadores em producao devem tratar este warning como
    pre-condicao obrigatoria.
    """
    from app.config import settings

    if not settings.redis_url:
        _logger.warning(
            "[startup] REDIS_URL nao configurada. "
            "Impactos em deploy com replicas > 1: "
            "(1) Idempotencia distribuida de eventos (P1.1) desabilitada — "
            "re-entrega do mesmo cloudevents.id pode gerar diagnosticos duplicados. "
            "(2) A2A TaskStore em memoria (P1.2) — GET /a2a/task/{id} pode retornar "
            "404 quando a task foi criada por outro pod. "
            "Para deploy de producao: configure REDIS_URL no .env / ConfigMap."
        )
