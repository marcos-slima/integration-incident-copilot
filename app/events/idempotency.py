"""Idempotencia distribuida para eventos de incidente (P1.1).

Antes desta fase, handle_incident_event() usava um set Python em memoria
(_SEEN_EVENT_IDS) para evitar reprocessar o mesmo cloudevents.id. Isso
funcionava para um unico pod/processo, mas falha silenciosamente em deploy
Kyma com replicas > 1: cada pod tem seu proprio set, entao o mesmo evento
pode ser processado duas vezes por pods diferentes.

P1.1 substitui o set em memoria por um Redis Set (SADD + TTL), que e
compartilhado entre todos os pods. A semantica e identica, a durabilidade e
superior:

  - SADD event_id retorna 1 se o evento era novo, 0 se ja estava no set.
  - TTL de 24h garante que o set nao cresca sem limite (eventos mais
    antigos que 24h raramente sao reentregues por qualquer broker).
  - Fallback: se Redis nao estiver disponivel (REDIS_URL vazio ou Redis
    fora do ar), is_duplicate() sempre retorna False — comportamento
    pre-P1.1, preservando a disponibilidade em ambiente de desenvolvimento
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

_logger = logging.getLogger(__name__)

# Prefixo de namespace Redis — evita colisao com chaves do RQ ("rq:*")
# e do TaskStore A2A ("a2a:task:*")
_REDIS_KEY = "events:seen_ids"

# TTL do set de idempotencia: 24h. Mesmo evento reentregue depois de 24h
# (raro, mas possivel em brokers com politica de retry longa) sera
# reprocessado — aceitavel dado que o objetivo e deduplicar entregas
# rapidas, nao auditoria de longo prazo.
_IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60

# Lazy: evita import de redis no startup quando REDIS_URL nao esta
# configurada (mesmo padrao de _load_queue() em consumer.py e
# _get_redis_client() em a2a/task_store.py)
_redis_client = None
_redis_available: bool | None = None  # None = nao verificado ainda


def _get_client():
    """Retorna o cliente Redis, criando-o na primeira chamada.
    Retorna None se Redis nao estiver disponivel ou configurado."""
    global _redis_client, _redis_available

    if _redis_available is False:
        return None
    if _redis_client is not None:
        return _redis_client

    from app.config import settings

    if not settings.redis_url:
        _redis_available = False
        return None

    try:
        import redis

        _redis_client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        # Ping para verificar conectividade na primeira chamada
        _redis_client.ping()
        _redis_available = True
        return _redis_client
    except Exception as exc:
        _logger.warning(
            "[idempotency] Redis nao disponivel — idempotencia distribuida "
            "desabilitada (fallback: sem deduplicacao entre pods). "
            "Configure REDIS_URL para habilitar. erro=%s",
            exc,
        )
        _redis_available = False
        return None


def is_duplicate(event_id: str) -> bool:
    """Verifica se o evento ja foi visto e registra o ID no Redis Set.

    Retorna True se o evento ja foi processado (duplicado), False se e novo.

    Semantica atomica: SADD retorna 1 (novo) ou 0 (duplicado) em uma
    operacao atomica, sem race condition entre pods.

    Se Redis nao estiver disponivel, retorna False (sem deduplicacao) —
    mesmo comportamento do set em memoria pre-P1.1 para um unico pod.
    """
    client = _get_client()
    if client is None:
        return False  # fallback: nao deduplica, processa normalmente

    try:
        # SADD retorna o numero de elementos adicionados (1 = novo, 0 = duplicado)
        added = client.sadd(_REDIS_KEY, event_id)
        if added == 0:
            _logger.info(
                "[idempotency] Evento duplicado detectado — ignorando. " "cloudevents.id=%s",
                event_id,
            )
            return True
        # Renova o TTL a cada novo evento inserido — garante que o set
        # nao expire enquanto eventos recentes ainda estao sendo processados
        client.expire(_REDIS_KEY, _IDEMPOTENCY_TTL_SECONDS)
        return False
    except Exception as exc:
        _logger.warning(
            "[idempotency] Erro ao verificar idempotencia no Redis — "
            "processando evento normalmente (fail-open). "
            "cloudevents.id=%s erro=%s",
            event_id,
            exc,
        )
        return False  # fail-open: prefere reprocessar a perder evento


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
