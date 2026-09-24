"""SAP Integration Copilot - entrypoint FastAPI."""

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from langfuse import get_client
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.a2a.agent_card import get_agent_card
from app.a2a.server import router as a2a_router
from app.agent.graph import run_diagnosis
from app.config import settings
from app.connectors import connector_status
from app.db import AsyncSessionLocal, is_db_enabled
from app.events.amqp_consumer import amqp_consumer  # DA-32
from app.events.consumer import handle_incident_event
from app.events.idempotency import _warn_if_redis_missing_with_replicas
from app.exceptions import DiagnosisTimeoutError
from app.mcp.server import build_mcp_asgi_app
from app.mcp.server import mcp as mcp_server
from app.metrics import (
    DIAGNOSIS_LATENCY,
    DIAGNOSIS_TOTAL,
    DIAGNOSIS_VERIFIED_TOTAL,
    PII_DETECTED_TOTAL,
    REDACTION_APPLIED_TOTAL,
    SENSITIVE_INCIDENT_TOTAL,
    setup_metrics,
)
from app.models import (
    DiagnosisResponse,
    IncidentEventEnvelope,
    IncidentRequest,
    VerifyIncidentRequest,
)
from app.queue import AsyncQueueUnavailableError, enqueue_diagnosis, get_job_status
from app.rag.graph_store import GRAPH_UNAVAILABLE_EXCEPTIONS, ensure_constraints, verify_incident
from app.rate_limit import limiter
from app.services.incident_repository import IncidentRepository

logger = logging.getLogger(__name__)

# ─── API Key (opcional) ────────────────────────────────────────────────────
# Se API_KEY nao estiver configurado no .env, autenticacao e desabilitada.
# Para habilitar: API_KEY=sua-chave-secreta no .env
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
event_mesh_api_key_header = APIKeyHeader(name="X-Event-Mesh-Api-Key", auto_error=False)


def verify_api_key(api_key: str | None = Security(api_key_header)) -> None:
    """Valida API Key. DA-18: apos _ensure_api_keys_configured() rodar
    no startup, settings.api_key NUNCA fica vazio - nao ha mais "modo
    aberto" silencioso. Comparacao com secrets.compare_digest (nao
    "==") para nao vazar o tamanho/prefixo da chave via timing attack."""
    configured_key = settings.api_key
    if not secrets.compare_digest(api_key or "", configured_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key invalida ou ausente",
        )


def verify_event_mesh_api_key(api_key: str | None = Security(event_mesh_api_key_header)) -> None:
    """DA-23: chave DEDICADA para o webhook de eventos - nao reaproveita
    verify_api_key/API_KEY, para que um webhook secret vazado (exposto
    na configuracao do sistema de monitoracao externo que publica os
    eventos) nao comprometa o endpoint /diagnose humano nem o A2A."""
    configured_key = settings.event_mesh_api_key
    if not secrets.compare_digest(api_key or "", configured_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Event-Mesh-Api-Key invalida ou ausente",
        )


class MissingRequiredAuthError(RuntimeError):
    """Levantada no startup quando settings.require_auth=true e uma ou
    mais chaves de API obrigatorias nao foram configuradas - impede o
    lifespan do FastAPI de completar, o que faz o processo uvicorn
    sair sem subir a API (falha alta e imediata, nao um log que pode
    passar despercebido)."""


def _ensure_api_keys_configured() -> None:
    """DA-18: nenhum endpoint protegido (/diagnose via API_KEY, /a2a
    via A2A_API_KEY) deve ficar sem NENHUMA chave em memoria. Antes,
    A2A_API_KEY/API_KEY vazios no .env significavam autenticacao
    completamente desabilitada (qualquer chamador passava) - um gap
    silencioso, so visivel lendo o codigo-fonte. Se o operador nao
    configurou uma chave, geramos uma aleatoria por processo aqui e
    avisamos ALTO no log de startup - preserva o "clone e rode" (zero
    config obrigatoria pra rodar local) sem deixar os endpoints
    abertos por padrao. A chave gerada muda a cada restart; para uma
    chave estavel, configure API_KEY/A2A_API_KEY no .env.

    Avaliacao externa (curto prazo, item 1): com settings.require_auth
    ligado, uma chave efemera gerada aqui NAO e aceitavel - o operador
    pediu explicitamente que o processo RECUSE subir em vez de rodar
    com uma chave que ninguem documentou/distribuiu. Verificado ANTES
    de qualquer geracao automatica, para as 3 chaves (api_key,
    a2a_api_key, event_mesh_api_key - a ultima nao fazia parte do
    pedido original da revisao, mas e a mesma categoria de risco;
    ficar de fora seria inconsistente)."""
    if settings.require_auth:
        missing = [
            name
            for name, value in (
                ("API_KEY", settings.api_key),
                ("A2A_API_KEY", settings.a2a_api_key),
                ("EVENT_MESH_API_KEY", settings.event_mesh_api_key),
            )
            if not value
        ]
        if missing:
            raise MissingRequiredAuthError(
                "REQUIRE_AUTH=true, mas as seguintes chaves nao estao "
                f"configuradas no .env: {', '.join(missing)}. Configure-as "
                "explicitamente (nao ha geracao automatica de chave efemera "
                "quando REQUIRE_AUTH esta ligado) ou desligue REQUIRE_AUTH "
                "para o comportamento default de desenvolvimento."
            )
    if not settings.api_key:
        settings.api_key = secrets.token_urlsafe(32)
        # B7: nunca logue o valor da chave — logs sao frequentemente
        # agregados em sistemas externos (Datadog, Grafana Loki) e o valor
        # pode vazar. Para recuperar a chave gerada, use a variavel de
        # ambiente API_KEY no .env; reiniciar o processo gera uma nova chave.
        logger.warning(
            "API_KEY nao configurada no .env — chave efemera gerada para "
            "esta execucao. Configure API_KEY no .env para chave estavel, "
            "ou REQUIRE_AUTH=true para recusar subir sem chave explicita."
        )
    if not settings.a2a_api_key:
        settings.a2a_api_key = secrets.token_urlsafe(32)
        logger.warning(
            "A2A_API_KEY nao configurada no .env — chave efemera gerada para "
            "esta execucao. Configure A2A_API_KEY no .env para chave estavel, "
            "ou REQUIRE_AUTH=true para recusar subir sem chave explicita."
        )
    if not settings.event_mesh_api_key:
        settings.event_mesh_api_key = secrets.token_urlsafe(32)
        logger.warning(
            "EVENT_MESH_API_KEY nao configurada no .env — chave efemera gerada "
            "para esta execucao. Configure EVENT_MESH_API_KEY no .env para "
            "chave estavel, ou REQUIRE_AUTH=true para recusar subir sem chave "
            "explicita."
        )


class MissingGraphRagCredentialsError(RuntimeError):
    """Levantada no startup quando GRAPH_RAG_ENABLED=true mas
    NEO4J_PASSWORD nao foi configurada - impede o lifespan de
    completar, mesmo principio de "falhar alto e cedo" de
    MissingRequiredAuthError acima.

    Avaliacao externa (curto prazo, item 6): o Neo4j do
    docker-compose.yml (perfil "graphrag") exige NEO4J_PASSWORD
    explicitamente (sem fallback fraco "changeme123" - ver
    docker-compose.yml). Sem esta checagem, um NEO4J_PASSWORD vazio
    aqui do lado do app faria o driver tentar autenticar com senha
    vazia contra QUALQUER Neo4j configurado em NEO4J_URI - inclusive
    um Neo4j de terceiros fora deste docker-compose.yml, onde nao ha
    garantia nenhuma de que senha vazia falhe alto (alguns setups
    self-managed permitem auth desabilitada). Melhor recusar subir
    aqui do que depender do Neo4j do outro lado rejeitar a conexao."""


def _ensure_graph_rag_password_configured() -> None:
    if settings.graph_rag_enabled and not settings.neo4j_password:
        raise MissingGraphRagCredentialsError(
            "GRAPH_RAG_ENABLED=true, mas NEO4J_PASSWORD nao esta configurada "
            "no .env. Configure-a explicitamente (o Neo4j do docker-compose.yml, "
            'perfil "graphrag", tambem exige NEO4J_PASSWORD, sem default fraco) '
            "ou desligue GRAPH_RAG_ENABLED."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_api_keys_configured()
    _ensure_graph_rag_password_configured()
    # P1.1: avisa sobre ausencia de Redis em producao multi-replica
    _warn_if_redis_missing_with_replicas()
    # DA-21: garante os constraints/indices do Neo4j no startup quando
    # GraphRAG esta habilitado, eliminando o passo manual
    # `python -m app.rag.graph_store --init`. Envolvido em try/except
    # porque um Neo4j temporariamente fora do ar NAO pode impedir a API
    # de subir - graph_enrich_node/graph_write_node ja degradam
    # graciosamente (ver DA-21 em app/agent/nodes.py) quando o grafo
    # esta indisponivel, entao o startup segue a mesma filosofia.
    if settings.graph_rag_enabled:
        try:
            ensure_constraints()
        except GRAPH_UNAVAILABLE_EXCEPTIONS as exc:
            logger.warning(
                "Neo4j indisponivel no startup - constraints/indices do "
                "GraphRAG nao foram verificados agora (tentaremos de novo "
                "de forma implicita nas proximas escritas/leituras): %s",
                exc,
            )
    # DA-19: o app ASGI do servidor MCP (app/mcp/server.py) gerencia
    # sessoes via StreamableHTTPSessionManager, que precisa do seu
    # proprio lifespan rodando - `app.mount()` NAO propaga eventos de
    # lifespan para sub-apps automaticamente (limitacao do Starlette),
    # entao entramos nesse contexto aqui, no lifespan do app raiz.
    # ATENCAO (testes): `session_manager.run()` so pode ser chamado UMA
    # vez por instancia de processo - o SDK MCP levanta RuntimeError na
    # segunda tentativa. Como `app` e um singleto global, so pode haver
    # UM `with TestClient(app) as ...` (que dispara o lifespan) em toda
    # a suite de testes - ver tests/test_mcp.py.
    async with mcp_server.session_manager.run():
        await amqp_consumer.start()  # DA-32: inicia consumidor AMQP (no-op se AMQP_ENABLED=false)
        try:
            yield
        finally:
            await amqp_consumer.stop()  # DA-32: graceful shutdown
    get_client().flush()


app = FastAPI(
    title="SAP Integration Copilot",
    description="Assistente de IA para diagnostico de incidentes de integracao SAP",
    version="1.2.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
# Avaliacao externa (medio prazo, item 1): sem este middleware,
# "default_limits" do Limiter (app/rate_limit.py) so valia para rotas
# com "@limiter.limit(...)" explicito - /a2a, /mcp e /health ficavam
# sem NENHUM rate limit. Com o middleware, o default passa a valer
# tambem para essas rotas (/a2a ainda ganha seu proprio decorator
# explicito, ver app/a2a/server.py, pelo mesmo motivo de /diagnose ja
# ter um).
app.add_middleware(SlowAPIMiddleware)

# Fase 2 Observabilidade: Prometheus /metrics (opt-in via PROMETHEUS_ENABLED=true)
setup_metrics(app)


@app.exception_handler(DiagnosisTimeoutError)
def _diagnosis_timeout_handler(request: Request, exc: DiagnosisTimeoutError):
    """Avaliacao externa (curto prazo, item 4): converte o watchdog de
    settings.diagnosis_timeout_seconds (app/agent/graph.py) num 504
    Gateway Timeout explicito, em vez de deixar virar um 500 generico -
    o caller (humano ou outro sistema) precisa distinguir "o pipeline
    demorou demais" de "o pipeline quebrou". Cobre /diagnose e
    /events/incident (ambos chamam run_diagnosis diretamente); o A2A
    (app/a2a/task_manager.py) ja captura Exception por conta propria e
    marca a task como failed, sem passar por aqui."""
    return JSONResponse(status_code=status.HTTP_504_GATEWAY_TIMEOUT, content={"detail": str(exc)})


# Avaliacao externa (nova revisao, P0 - "CI e a API nao sobem sem o
# bundle Vite"): static/dist/ e gerado por "npm run build" (nao
# versionado no git - .gitignore:5 tem uma regra generica "dist/" que
# tambem pega static/dist/, alem de dist/ do Python) e so existe de
# verdade depois desse build (local, no Dockerfile multi-stage, ou
# agora tambem no CI - ver .github/workflows/tests.yml). Antes desta
# mudanca, o mount abaixo rodava incondicionalmente NA IMPORTACAO do
# modulo - "import app.main" (o que TODO teste de tests/test_api.py,
# tests/test_a2a.py etc faz, via "from app.main import app") explodia
# com RuntimeError em qualquer checkout sem o bundle ja buildado a
# mao, inclusive potencialmente no CI. Agora o mount so acontece se o
# diretorio existir - clonar o repo e rodar so a API/os testes
# (cenario "so backend", sem Node/npm instalado) continua funcionando;
# "/" devolve 404 com uma mensagem clara em vez de FileNotFoundError
# se ninguem buildou o frontend ainda.
_STATIC_DIST_ASSETS_DIR = Path("static/dist/assets")
_STATIC_DIST_INDEX = Path("static/dist/index.html")

if _STATIC_DIST_ASSETS_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_STATIC_DIST_ASSETS_DIR)), name="assets")
else:
    logger.warning(
        "static/dist/assets nao encontrado - bundle do frontend (frontend/) nao foi "
        "buildado ('npm ci && npm run build' em frontend/, ou 'docker compose build'). "
        "A API sobe normalmente, mas GET / devolvera 404 ate o bundle existir."
    )


@app.get("/", response_model=None)
def index() -> FileResponse | JSONResponse:
    if not _STATIC_DIST_INDEX.is_file():
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "detail": (
                    "Frontend nao buildado (static/dist/index.html ausente). Rode "
                    "'npm ci && npm run build' em frontend/, ou use a imagem Docker "
                    "(que builda o frontend automaticamente)."
                )
            },
        )
    return FileResponse(str(_STATIC_DIST_INDEX))


def _probe_infra_services() -> dict[str, str]:
    """Testa conectividade com Qdrant e com o provider LLM configurado.

    DA-39: antes checava Ollama incondicionalmente - um pod Kyma com
    LLM_PROVIDER=openai ficava sempre "degraded" por nao ter OLLAMA_HOST,
    o que deixava o readinessProbe do Kubernetes retornando 503 e o pod
    nunca passava para "Ready". Agora a checagem e provider-aware:
    - ollama -> proba /api/tags no OLLAMA_HOST configurado
    - openai -> verifica se OPENAI_API_KEY foi configurada (nao faz chamada
      de rede para nao expor a key e nao adicionar latencia/custo)
    - azure_openai -> idem para AZURE_OPENAI_API_KEY
    """
    import httpx  # import local: mantém ordenacao de imports sem quebrar isort

    results: dict[str, str] = {}
    _timeout = httpx.Timeout(1.0)

    if settings.qdrant_url:
        try:
            r = httpx.get(f"{settings.qdrant_url.rstrip('/')}/healthz", timeout=_timeout)
            results["qdrant"] = "ok" if r.is_success else "degraded"
        except (OSError, httpx.HTTPError):
            results["qdrant"] = "degraded"
    else:
        results["qdrant"] = "not_configured"

    # DA-39: checagem do LLM depende do provider configurado
    provider = settings.llm_provider
    if provider == "ollama":
        if settings.ollama_host:
            try:
                r = httpx.get(f"{settings.ollama_host.rstrip('/')}/api/tags", timeout=_timeout)
                results["ollama"] = "ok" if r.is_success else "degraded"
            except (OSError, httpx.HTTPError):
                results["ollama"] = "degraded"
        else:
            results["ollama"] = "not_configured"
    elif provider == "openai":
        results["openai"] = "ok" if settings.openai_api_key else "not_configured"
    elif provider == "azure_openai":
        results["azure_openai"] = "ok" if settings.azure_openai_api_key else "not_configured"
    else:
        results[provider] = "unknown_provider"

    return results


@app.get("/health")
def health() -> dict:
    """Alem do status geral, devolve o estado real (derivado do .env
    atual, ver app.connectors.connector_status) de cada conector e das
    principais flags de infraestrutura - fonte que o frontend
    (StatusView) consulta em vez de manter uma lista hardcoded que
    nao reflete o backend de verdade.

    DA-35: probe real em Qdrant/Ollama via _probe_infra_services() —
    o /health agora reflete disponibilidade real, nao so configuracao.
    Quando algum servico esta degradado, "status" passa a "degraded"
    (nao "ok") para que load balancers e liveness probes detectem."""
    infra_probes = _probe_infra_services()
    all_ok = all(v == "ok" for v in infra_probes.values() if v != "not_configured")
    overall = "ok" if all_ok else "degraded"

    return {
        "status": overall,
        "connectors": connector_status(),
        "infra": {
            "llm_provider": settings.llm_provider,
            "graph_rag_enabled": settings.graph_rag_enabled,
            "langfuse_enabled": bool(settings.langfuse_public_key and settings.langfuse_secret_key),
            "async_queue_enabled": bool(settings.redis_url),
            "auth_required": bool(settings.api_key),
        },
        "services": infra_probes,
    }


@app.get("/ready")
def ready() -> dict:
    """Readiness probe — verifica se os servicos de infraestrutura estao acessiveis.

    Diferente de /health (liveness, sem I/O externo), este endpoint faz probes
    reais em Qdrant e no LLM provider. O Kubernetes so roteia trafego para o pod
    quando este endpoint retorna 2xx.

    Retorna 503 se algum servico obrigatorio estiver degradado, removendo o pod
    do load balancer ate a recuperacao.
    """
    infra_probes = _probe_infra_services()
    degraded = [k for k, v in infra_probes.items() if v not in ("ok", "not_configured")]
    if degraded:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail={"status": "not_ready", "degraded": degraded, "services": infra_probes},
        )
    return {"status": "ready", "services": infra_probes}


@app.post(
    "/diagnose",
    response_model=DiagnosisResponse,
    dependencies=[Depends(verify_api_key)],
)
@limiter.limit("10/minute")
def diagnose(request: Request, body: IncidentRequest) -> DiagnosisResponse:
    """Diagnostica um incidente de integracao SAP.

    Rate limit: 10 requisicoes por minuto por IP.
    Autenticacao: X-API-Key header (se API_KEY configurado no .env).
    """
    import time

    _t0 = time.monotonic()
    result = run_diagnosis(body)
    _latency_ms = int((time.monotonic() - _t0) * 1000)

    # Métricas Prometheus (Fase 2 Observabilidade) — COI/IOC + SOC + iPaaS
    _agent = result.agent_domain or "unknown"
    _provider = result.llm_provider_used or "unknown"
    _strength = result.evidence_strength or "unknown"
    _sensitivity = getattr(body, "sensitivity_level", None) or "unclassified"
    _pii = getattr(body, "pii_detected", False)
    _redacted = getattr(body, "redaction_applied", False)

    DIAGNOSIS_TOTAL.labels(
        agent_domain=_agent,
        llm_provider=_provider,
        evidence_strength=_strength,
    ).inc()

    DIAGNOSIS_LATENCY.labels(
        agent_domain=_agent,
        llm_provider=_provider,
    ).observe(_latency_ms / 1000.0)

    if _pii:
        PII_DETECTED_TOTAL.labels(sensitivity_level=_sensitivity).inc()
    if _redacted:
        REDACTION_APPLIED_TOTAL.labels(sensitivity_level=_sensitivity).inc()
    if _sensitivity in ("confidential", "secret"):
        SENSITIVE_INCIDENT_TOTAL.labels(sensitivity_level=_sensitivity).inc()

    # Persistência opt-in (Fase 1 Observabilidade Grafana)
    if is_db_enabled() and AsyncSessionLocal is not None:
        import asyncio

        async def _persist() -> None:
            async with AsyncSessionLocal() as _session:
                try:
                    repo = IncidentRepository(_session)
                    _evidence_list = [
                        {"source": e.source, "content": e.content} for e in (result.evidence or [])
                    ]
                    await repo.create(
                        interface_type=getattr(body, "interface_type", None),
                        description=getattr(body, "description", None),
                        connector_source_system=getattr(body, "connector_source_system", None),
                        is_mock=getattr(body, "use_mock", True),
                        sensitivity_level=getattr(body, "sensitivity_level", None),
                        trace_id=result.trace_id,
                        probable_root_cause=result.probable_root_cause,
                        model_confidence=result.model_confidence,
                        diagnosis_confidence=result.diagnosis_confidence,
                        evidence_strength=result.evidence_strength,
                        llm_provider_used=result.llm_provider_used,
                        agent_domain=result.agent_domain,
                        evidence_json=_evidence_list if _evidence_list else None,
                        latency_ms=_latency_ms,
                    )
                    await _session.commit()
                except Exception:
                    logger.warning("Falha ao persistir incidente no PostgreSQL", exc_info=True)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_persist())
            else:
                loop.run_until_complete(_persist())
        except Exception:
            logger.warning("Falha ao agendar persistência assíncrona", exc_info=True)

    return result


@app.post(
    "/diagnose/async",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_api_key)],
)
@limiter.limit("10/minute")
def diagnose_async(request: Request, body: IncidentRequest) -> dict[str, str]:
    """Avaliacao externa (medio prazo, item 6 - "Fila assincrona"):
    versao assincrona de /diagnose - enfileira o diagnostico via RQ
    (mesmo Redis de app/a2a/task_store.py, ver app/queue.py) e devolve
    um job_id para consulta via GET /diagnose/async/{job_id}, em vez
    de bloquear a requisicao ate o LLM terminar (o /diagnose sincrono
    continua existindo sem nenhuma mudanca, para quem prefere/precisa
    de resposta imediata).

    Requer REDIS_URL configurada (503 caso contrario) e um worker RQ
    rodando para de fato processar o job (docker-compose.yml, servico
    "worker", profile "async") - sem worker, o job fica "queued"
    indefinidamente.

    Rate limit: 10 requisicoes por minuto por IP (mesma politica de
    /diagnose).
    Autenticacao: X-API-Key header (se API_KEY configurado no .env).
    """
    try:
        job_id = enqueue_diagnosis(body.model_dump())
    except AsyncQueueUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return {"job_id": job_id, "status": "queued"}


@app.get(
    "/diagnose/async/{job_id}",
    dependencies=[Depends(verify_api_key)],
)
def diagnose_async_status(job_id: str) -> dict[str, object]:
    """Consulta (polling) o status/resultado de um diagnostico
    enfileirado via POST /diagnose/async. Formato da resposta:
    {"job_id", "status", "result", "error"} - "status" e um dos
    valores do RQ ("queued", "started", "finished", "failed", etc.),
    "result" (o DiagnosisResponse serializado) so e preenchido quando
    status == "finished", "error" so quando status == "failed".

    404 se job_id nao existir (id invalido, ou resultado ja expirado -
    RQ mantem jobs finalizados por um TTL default).
    Autenticacao: X-API-Key header (mesma dependency de /diagnose).
    """
    try:
        job_status = get_job_status(job_id)
    except AsyncQueueUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    if job_status is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' nao encontrado."
        )
    return job_status


@app.post(
    "/events/incident",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_event_mesh_api_key)],
)
@limiter.limit("10/minute")
def incident_event_webhook(request: Request, envelope: IncidentEventEnvelope) -> dict[str, object]:
    """DA-23 (Event Mesh) + P0.4 (revisao arquitetural, 23/09/2026).

    Ingestao orientada a evento: recebe um envelope CloudEvents
    (formato usado pelo SAP Event Mesh em modo REST/Webhook push
    subscription) representando uma falha de integracao detectada
    por um sistema de monitoracao externo.

    P0.4: retorna 202 Accepted + job_id imediatamente apos enfileirar
    o diagnostico via Redis/RQ. O diagnostico roda de forma duravel
    no worker (profile "async") — pod crash nao perde o evento.
    Fallback: sem Redis, executa inline (pre-P0.4) com warning no log.

    Idempotencia: cloudevents.id e usado como job_id — re-entrega do
    mesmo evento nao cria um segundo diagnostico.

    Rate limit: 10 requisicoes por minuto por IP.
    Autenticacao: X-Event-Mesh-Api-Key header (DA-23), chave dedicada
    isolada de API_KEY/A2A_API_KEY.
    """
    return handle_incident_event(envelope)


@app.post(
    "/incidents/{incident_id}/verify",
    dependencies=[Depends(verify_api_key)],
)
@limiter.limit("10/minute")
def verify_incident_endpoint(
    request: Request, incident_id: str, body: VerifyIncidentRequest
) -> dict[str, str | bool]:
    """DA-28 (VERIFIED_AS) + avaliacao externa (medio prazo, item 5 -
    "Metricas e feedback"): registra a confirmacao EXPLICITA (humana ou
    de outro sistema) da causa raiz de um incidente ja diagnosticado, e
    opcionalmente um veredito de correto/incorreto (`body.correct`)
    como score no trace Langfuse original (`body.trace_id`).

    Dois efeitos INDEPENDENTES (ver docstring de VerifyIncidentRequest):
    grafo (Neo4j, exige GraphRAG ligado + incident_id valido) e score
    Langfuse (exige trace_id). Nenhum bloqueia o outro - um cliente que
    so tem trace_id (GraphRAG desligado) ainda registra feedback no
    Langfuse; um cliente que so tem incident_id ainda grava no grafo,
    exatamente como antes desta mudanca (DA-28).

    400 se nenhum dos dois puder acontecer - GraphRAG desligado (ou
    sem tentativa de grafo porque nao houve incident_id gravavel) E
    trace_id ausente, ou seja, a chamada nao tem NENHUMA pre-condicao
    atendida para fazer alguma coisa.
    404 se GraphRAG estiver ligado mas o `incident_id` nao existir no
    grafo - mesmo comportamento estrito de antes (nao silencioso, o
    operador chamou isto esperando um efeito real).

    Rate limit: 10 requisicoes por minuto por IP (mesma politica dos
    demais endpoints mutantes).
    Autenticacao: X-API-Key header (mesma dependency de /diagnose).
    """
    graph_updated = False
    if settings.graph_rag_enabled:
        graph_updated = verify_incident(
            incident_id=incident_id,
            verified_root_cause=body.root_cause,
            verified_by=body.verified_by,
        )
        if not graph_updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Incidente '{incident_id}' nao encontrado no grafo.",
            )

    langfuse_scored = False
    if body.trace_id and body.correct is not None:
        try:
            get_client().create_score(
                trace_id=body.trace_id,
                name="diagnosis_correct",
                value=body.correct,
                data_type="BOOLEAN",
                comment=body.root_cause,
            )
            langfuse_scored = True
        except Exception:
            logger.warning(
                "Falha ao gravar score de feedback no Langfuse (trace_id=%s)",
                body.trace_id,
                exc_info=True,
            )

    if not settings.graph_rag_enabled and not langfuse_scored:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Nada para registrar: GraphRAG esta desligado "
                "(GRAPH_RAG_ENABLED=false) e nenhum trace_id/correct valido "
                "foi informado para gravar feedback no Langfuse."
            ),
        )

    # Persistência opt-in (Fase 1 Observabilidade Grafana)
    if is_db_enabled() and AsyncSessionLocal is not None:
        import asyncio

        async def _update_verification() -> None:
            async with AsyncSessionLocal() as _session:
                try:
                    repo = IncidentRepository(_session)
                    await repo.update_verification(
                        incident_id,
                        diagnosis_correct=body.correct if body.correct is not None else True,
                        verified_by=body.verified_by,
                        verified_root_cause=body.root_cause,
                    )
                    await _session.commit()
                except Exception:
                    logger.warning(
                        "Falha ao atualizar verificação no PostgreSQL (incident_id=%s)",
                        incident_id,
                        exc_info=True,
                    )

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_update_verification())
            else:
                loop.run_until_complete(_update_verification())
        except Exception:
            logger.warning("Falha ao agendar atualização de verificação", exc_info=True)

    # Métrica de verificação humana (COI/IOC)
    _verdict = "correct" if (body.correct is True) else "incorrect"
    DIAGNOSIS_VERIFIED_TOTAL.labels(
        agent_domain="unknown",  # agent_domain não está no VerifyIncidentRequest
        verdict=_verdict,
    ).inc()

    return {
        "incident_id": incident_id,
        "status": "verified",
        "graph_updated": graph_updated,
        "langfuse_scored": langfuse_scored,
    }


@app.get("/.well-known/agent-card.json")
def agent_card() -> dict:
    return get_agent_card()


app.include_router(a2a_router)

# DA-19: servidor MCP montado em /mcp - ver app/mcp/server.py para o
# contrato de ferramentas (diagnose_incident, list_connectors) e a
# justificativa arquitetural completa.
app.mount("/mcp", build_mcp_asgi_app())
