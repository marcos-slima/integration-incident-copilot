"""SAP Integration Copilot - entrypoint FastAPI."""

import logging
import secrets
from contextlib import asynccontextmanager

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
from app.events.consumer import handle_incident_event
from app.exceptions import DiagnosisTimeoutError
from app.mcp.server import build_mcp_asgi_app
from app.mcp.server import mcp as mcp_server
from app.models import (
    DiagnosisResponse,
    IncidentEventEnvelope,
    IncidentRequest,
    VerifyIncidentRequest,
)
from app.rag.graph_store import GRAPH_UNAVAILABLE_EXCEPTIONS, ensure_constraints, verify_incident
from app.rate_limit import limiter

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
        logger.warning(
            "API_KEY nao configurada no .env - chave gerada automaticamente "
            "para esta execucao (header X-API-Key): %s",
            settings.api_key,
        )
    if not settings.a2a_api_key:
        settings.a2a_api_key = secrets.token_urlsafe(32)
        logger.warning(
            "A2A_API_KEY nao configurada no .env - chave gerada automaticamente "
            "para esta execucao (header X-A2A-Api-Key): %s",
            settings.a2a_api_key,
        )
    if not settings.event_mesh_api_key:
        settings.event_mesh_api_key = secrets.token_urlsafe(32)
        logger.warning(
            "EVENT_MESH_API_KEY nao configurada no .env - chave gerada "
            "automaticamente para esta execucao (header X-Event-Mesh-Api-Key): %s",
            settings.event_mesh_api_key,
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
        yield
    get_client().flush()


app = FastAPI(
    title="SAP Integration Copilot",
    description="Assistente de IA para diagnostico de incidentes de integracao SAP",
    version="0.1.0",
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


# Serve assets do bundle Vite (JS, CSS, fontes)
app.mount("/assets", StaticFiles(directory="static/dist/assets"), name="assets")


@app.get("/")
def index() -> FileResponse:
    return FileResponse("static/dist/index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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
    return run_diagnosis(body)


@app.post(
    "/events/incident",
    response_model=DiagnosisResponse,
    dependencies=[Depends(verify_event_mesh_api_key)],
)
@limiter.limit("10/minute")
def incident_event_webhook(request: Request, envelope: IncidentEventEnvelope) -> DiagnosisResponse:
    """DA-23 (Event Mesh) - ingestao orientada a evento: recebe um
    envelope CloudEvents (formato usado pelo SAP Event Mesh em modo
    REST/Webhook push subscription) representando uma falha de
    integracao detectada por um sistema de monitoracao externo, e
    dispara run_diagnosis() automaticamente - sem chamada manual a
    /diagnose. So `type == "com.sap.integration.incident.detected.v1"`
    e aceito hoje; qualquer outro valor e rejeitado com 422 (ver
    IncidentEventEnvelope em app/models.py).

    Rate limit: 10 requisicoes por minuto por IP (mesma politica de
    /diagnose - uma fila real de eventos, se o volume justificar,
    trocaria isso por backpressure/processamento assincrono; nao e o
    caso hoje, escopo deliberadamente fora desta fase).
    Autenticacao: X-Event-Mesh-Api-Key header, chave dedicada e isolada
    de API_KEY/A2A_API_KEY.
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
