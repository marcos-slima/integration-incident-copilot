"""SAP Integration Copilot - entrypoint FastAPI."""

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from langfuse import get_client
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.a2a.agent_card import get_agent_card
from app.a2a.server import router as a2a_router
from app.admin.routes import router as admin_router  # DA-46/47/48
from app.admin.security import ensure_admin_key_configured
from app.admin.ui import ui_router as admin_ui_router
from app.agent.graph import run_diagnosis
from app.auth import ensure_session_secret_configured, verify_session_cookie
from app.auth import router as auth_router
from app.config import settings
from app.connectors import connector_status
from app.events import idempotency
from app.events.amqp_consumer import amqp_consumer  # DA-32
from app.events.consumer import handle_incident_event_async
from app.exceptions import ConfigurationError, DiagnosisTimeoutError
from app.llm.gateway import describe_effective_policy
from app.mcp.server import build_mcp_asgi_app
from app.mcp.server import mcp as mcp_server
from app.models import (
    DiagnosisResponse,
    IncidentEventEnvelope,
    IncidentRequest,
    VerifyIncidentRequest,
)
from app.queue import AsyncQueueUnavailableError, enqueue_diagnosis, get_job_status
from app.rag.graph_store import GRAPH_UNAVAILABLE_EXCEPTIONS, ensure_constraints, verify_incident
from app.rate_limit import limiter
from app.services.incident_recorder import record_verification  # DA-50

logger = logging.getLogger(__name__)

# ─── API Key (sempre exigida - DA-18) ──────────────────────────────────────
# Se API_KEY nao estiver configurado, _ensure_api_keys_configured gera uma
# chave aleatoria no startup (avisada em WARNING no log) - a autenticacao
# NUNCA fica desabilitada. Para uma chave fixa: API_KEY=... no .env
# (e repassada ao container via x-common-env no docker-compose.yml).
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


def verify_session_or_api_key(
    request: Request, api_key: str | None = Security(api_key_header)
) -> None:
    """DA-54 sobre DA-18, sem quebra: duas vias em /diagnose.

    - maquina: header X-API-Key, exatamente como antes (le o settings
      DESTE modulo — o que os testes monkeypatcham, mesmo padrao de
      verify_api_key);
    - humano: cookie de sessao HttpOnly (DA-54), validado em
      app/auth.py::verify_session_cookie.

    Aceitar o cookie NAO enfraquece o header: os dois exigem um segredo
    que o servidor emitiu/valida. MCP/A2A/Event Mesh/admin nao usam esta
    dependency — chaves dedicadas (DA-19/27/46)."""
    configured_key = settings.api_key
    # MESMA semantica de verify_api_key (DA-18), inclusive o vazio==vazio
    # confiar no startup: _ensure_api_keys_configured garante que o app
    # real nunca sobe com settings.api_key vazio — o guard e do lifespan,
    # nao da dependency (13 testes de test_api.py documentam isso).
    if secrets.compare_digest(api_key or "", configured_key):
        return
    if verify_session_cookie(request) is not None:
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="X-API-Key invalida/ausente e sem sessao valida (faca login em /auth/login)",
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
            "*" * len(settings.api_key),
        )
    if not settings.a2a_api_key:
        settings.a2a_api_key = secrets.token_urlsafe(32)
        logger.warning(
            "A2A_API_KEY nao configurada no .env - chave gerada automaticamente "
            "para esta execucao (header X-A2A-Api-Key): %s",
            "*" * len(settings.a2a_api_key),
        )
    if not settings.event_mesh_api_key:
        settings.event_mesh_api_key = secrets.token_urlsafe(32)
        logger.warning(
            "EVENT_MESH_API_KEY nao configurada no .env - chave gerada "
            "automaticamente para esta execucao (header X-Event-Mesh-Api-Key): %s",
            "*" * len(settings.event_mesh_api_key),
        )
    # DA-46/47/48: superficie admin (registro de modelos/credenciais/metering)
    # com chave DEDICADA. Mesmo contrato DA-18: nunca auth desabilitada.
    ensure_admin_key_configured()


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


def _ensure_evidence_key_configured() -> None:
    """DA-60: com persistencia ligada (DATABASE_URL) a evidencia de cada
    incidente e cifrada com LLM_CREDENTIALS_MASTER_KEY. Sem a chave o
    recorder nao teria como gravar a evidencia - falha no boot em vez de
    perder o dado em silencio a cada diagnostico."""
    if not settings.database_url:
        return
    from app.admin.crypto import _get_fernet

    try:
        _get_fernet()
    except ConfigurationError as exc:
        raise ConfigurationError(
            "DATABASE_URL configurada sem LLM_CREDENTIALS_MASTER_KEY valida: a "
            "evidencia dos incidentes e cifrada em repouso (DA-60). Gere com "
            '`python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"` e fixe no .env.'
        ) from exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_api_keys_configured()
    _ensure_evidence_key_configured()
    # DA-54: segredo HMAC dos cookies de sessao — vazio gera efemero
    # com WARNING, mesmo padrao DA-18 (nunca cookie sem assinatura)
    ensure_session_secret_configured()
    _ensure_graph_rag_password_configured()
    # P1.1: avisa quando idempotencia/A2A TaskStore ficam so em memoria
    idempotency._warn_if_redis_missing_with_replicas()
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
    if settings.langfuse_configured:
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


# Paginas servidas por este app (Admin UI Jinja2 e o bundle React) nao tem
# <script> inline nem handlers on*: todo JS vem de arquivo do proprio origin
# (/admin/static/*.js, /assets/*.js). As fontes do bundle React vem do Google
# Fonts (frontend/src/index.css), por isso os dois hosts abaixo.
_CSP_POLICY = (
    b"default-src 'self'; script-src 'self'; "
    b"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    b"font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; "
    b"connect-src 'self'; object-src 'none'; base-uri 'self'; "
    b"form-action 'self'; frame-ancestors 'none'"
)
# Swagger/ReDoc do FastAPI carregam JS e CSS de cdn.jsdelivr.net e usam
# script inline: a CSP estrita os deixaria em branco. Ficam de fora da
# politica (M-15 recomenda desliga-los em producao).
_CSP_EXEMPT_PREFIXES = ("/docs", "/redoc")


class CspMiddleware:
    """Content-Security-Policy e cabecalhos de endurecimento em respostas HTML."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path", "").startswith(_CSP_EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        async def send_with_csp(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                content_type = next((v for h, v in headers if h.lower() == b"content-type"), b"")
                if b"text/html" in content_type:
                    headers += [
                        (b"content-security-policy", _CSP_POLICY),
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"same-origin"),
                    ]
                    message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_csp)


app.add_middleware(CspMiddleware)


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
    """Testa conectividade real com Qdrant e Ollama (timeout curto para
    nao tornar o /ready lento). Retorna dict {servico: "ok"|"degraded"}.

    DA-35: /health antes retornava status="ok" sempre, independente de
    Qdrant/Ollama estarem acessiveis — nao era um readiness probe real.
    Agora faz GET nos endpoints de health de cada servico configurado,
    com timeout de 1s para nao impactar o tempo de resposta do /ready.
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

    if settings.ollama_host:
        try:
            r = httpx.get(f"{settings.ollama_host.rstrip('/')}/api/tags", timeout=_timeout)
            results["ollama"] = "ok" if r.is_success else "degraded"
        except (OSError, httpx.HTTPError):
            results["ollama"] = "degraded"
    else:
        results["ollama"] = "not_configured"

    # B-07: Redis sustenta fila de eventos, idempotencia e A2A TaskStore
    # quando configurado - indisponivel = pod nao pronto.
    if settings.redis_url:
        try:
            import redis

            redis.Redis.from_url(
                settings.redis_url, socket_connect_timeout=1, socket_timeout=1
            ).ping()
            results["redis"] = "ok"
        except Exception:  # noqa: BLE001 - qualquer falha de conexao/auth = degradado
            results["redis"] = "degraded"
    else:
        results["redis"] = "not_configured"

    # Langfuse - opcional, mas readiness deve representar se esta configurado
    # e acessivel (para tracing completo). Se Langfuse estiver fora, o app
    # continua funcionando (so perde tracing).
    if settings.langfuse_configured:
        try:
            from langfuse import get_client

            client = get_client()
            client.auth_check()
            results["langfuse"] = "ok"
        except Exception:  # noqa: BLE001 - qualquer falha = degradado
            results["langfuse"] = "degraded"
    else:
        results["langfuse"] = "not_configured"

    # Neo4j - opcional (GraphRAG), mas readiness deve representar se esta
    # configurado e acessivel. Se Neo4j estiver fora, GraphRAG falha, mas
    # o resto do app continua funcionando.
    if settings.graph_rag_enabled:
        if settings.neo4j_uri and settings.neo4j_user and settings.neo4j_password:
            try:
                from neo4j import GraphDatabase

                driver = GraphDatabase.driver(
                    settings.neo4j_uri,
                    auth=(settings.neo4j_user, settings.neo4j_password),
                    connection_timeout=1,
                )
                with driver.session() as session:
                    session.run("RETURN 1 AS val").single()
                results["neo4j"] = "ok"
            except Exception:  # noqa: BLE001 - qualquer falha = degradado
                results["neo4j"] = "degraded"
        else:
            results["neo4j"] = "not_configured"
    else:
        results["neo4j"] = "not_applicable"

    return results


def _required_services() -> set[str]:
    """B-07: dependencias obrigatorias para o modo ATIVO - "not_configured"
    so e aceitavel para as opcionais. Qdrant sempre (RAG e o nucleo do
    diagnostico); Ollama quando e o provider primario."""
    required = {"qdrant"}
    if settings.llm_provider == "ollama":
        required.add("ollama")
    if settings.graph_rag_enabled:
        required.add("neo4j")
    if settings.langfuse_configured:
        required.add("langfuse")
    return required


def _status_body() -> dict:
    """Estado derivado so de configuracao (.env) - sem IO externo."""
    return {
        "connectors": connector_status(),
        "infra": {
            "llm_provider": settings.llm_provider,
            "graph_rag_enabled": settings.graph_rag_enabled,
            "langfuse_enabled": settings.langfuse_configured,
            "async_queue_enabled": bool(settings.redis_url),
            "auth_required": bool(settings.api_key),
        },
    }


@app.get("/health")
def health() -> dict:
    """Liveness probe - confirma apenas que o processo FastAPI esta de pe.
    NAO faz chamadas externas (Qdrant/Ollama) e sempre retorna 200.

    §4.3 (avaliacao externa §3.6): o Kubernetes usa liveness para decidir
    se REINICIA o pod. Reiniciar nao resolve "Qdrant fora do ar", entao
    condicionar o liveness a dependencias externas causa restart-loop.
    Dependencias externas ficam em /ready (readiness).

    Tambem devolve o estado dos conectores/infra derivado do .env, que o
    frontend (StatusView) consulta."""
    return {"status": "ok", **_status_body()}


@app.get("/ready")
def ready() -> Response:
    """Readiness probe - probe real em Qdrant/Ollama/Redis (DA-35). Retorna
    503 quando algum servico configurado esta degradado (A-11) ou quando
    uma dependencia obrigatoria do modo ativo nao esta configurada
    (B-07, ver _required_services), para que o
    Kubernetes tire o pod do pool de roteamento ate a dependencia voltar,
    sem reinicia-lo (ver /health)."""
    infra_probes = _probe_infra_services()
    required = _required_services()
    all_ok = all(
        v == "ok"
        or (v == "not_configured" and name not in required)
        or (v == "not_applicable" and name not in required)
        for name, v in infra_probes.items()
    )
    body = {"status": "ok" if all_ok else "degraded", **_status_body(), "services": infra_probes}
    return JSONResponse(
        status_code=status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=body,
    )


@app.post(
    "/diagnose",
    response_model=DiagnosisResponse,
    dependencies=[Depends(verify_session_or_api_key)],
)
@limiter.limit("10/minute")
def diagnose(request: Request, body: IncidentRequest) -> DiagnosisResponse:
    """Diagnostica um incidente de integracao SAP.

    Rate limit: 10 requisicoes por minuto por IP.
    Autenticacao: X-API-Key header (se API_KEY configurado no .env).
    """
    return run_diagnosis(body)


@app.post(
    "/diagnose/async",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_session_or_api_key)],
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
    dependencies=[Depends(verify_session_or_api_key)],
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
def incident_event_webhook(
    request: Request,
    envelope: IncidentEventEnvelope,
    background_tasks: BackgroundTasks,
) -> Response:
    """DA-23 (Event Mesh) - ingestao orientada a evento: recebe um
    envelope CloudEvents (formato usado pelo SAP Event Mesh em modo
    REST/Webhook push subscription) representando uma falha de
    integracao detectada por um sistema de monitoracao externo, e
    dispara run_diagnosis() em background - sem chamada manual a
    /diagnose. So `type == "com.sap.integration.incident.detected.v1"`
    e aceito hoje; qualquer outro valor e rejeitado com 422 (ver
    IncidentEventEnvelope em app/models.py).

    §3.4 / B-02: responde 202 Accepted sem esperar o LLM. Com REDIS_URL,
    o 202 so sai depois do evento gravado na fila RQ (durable, retry,
    DLQ no FailedJobRegistry) e o corpo traz o job_id para consulta em
    GET /diagnose/async/{job_id}; falha no enqueue -> 503. Sem REDIS_URL
    (dev), usa BackgroundTasks no processo web - nao durable.

    Rate limit: 10 requisicoes por minuto por IP.
    Autenticacao: X-Event-Mesh-Api-Key header, chave dedicada e isolada
    de API_KEY/A2A_API_KEY.
    """
    try:
        body = handle_incident_event_async(envelope, background_tasks)
    except Exception as exc:
        # B-02: sem enqueue durable confirmado nao ha 202 - o publicador
        # (Event Mesh) recebe 503 e reenvia.
        logger.exception("[events] Falha ao enfileirar cloudevents.id=%s", envelope.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Fila de eventos indisponivel - reenvie o evento.",
        ) from exc
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=body)


@app.post(
    "/incidents/{incident_id}/verify",
    dependencies=[Depends(verify_session_or_api_key)],
)
@limiter.limit("10/minute")
def verify_incident_endpoint(
    request: Request, incident_id: str, body: VerifyIncidentRequest
) -> dict[str, str | bool]:
    """DA-28 (VERIFIED_AS) + avaliacao externa (medio prazo, item 5 -
    "Metricas e feedback") + DA-50: registra a confirmacao EXPLICITA (humana
    ou de outro sistema) da causa raiz de um incidente ja diagnosticado, e
    opcionalmente um veredito de correto/incorreto (`body.correct`)
    como score no trace Langfuse original (`body.trace_id`).

    Tres efeitos INDEPENDENTES (ver docstring de VerifyIncidentRequest):
    grafo (Neo4j, exige GraphRAG ligado + incident_id valido), score
    Langfuse (exige trace_id) e DA-50: gravacao na tabela `incidents`
    (best-effort, ver incident_recorder.record_verification). Nenhum
    bloqueia o outro - um cliente que so tem trace_id (GraphRAG desligado)
    ainda registra feedback no Langfuse; um cliente que so tem incident_id
    ainda grava no grafo.

    400 se nenhum dos tres puder acontecer - GraphRAG desligado (ou
    sem tentativa de grafo porque nao houve incident_id gravavel), sem
    gravacao no banco (sem DATABASE_URL ou incidente inexistente na tabela)
    E trace_id ausente, ou seja, a chamada nao tem NENHUMA pre-condicao
    atendida para fazer alguma coisa.
    404 se GraphRAG estiver ligado mas o `incident_id` nao existir no
    grafo - mesmo comportamento estrito de antes (nao silencioso, o
    operador chamou isto esperando um efeito real).

    Rate limit: 10 requisicoes por minuto por IP (mesma politica dos
    demais endpoints mutantes).
    Autenticacao: X-API-Key header (mesma dependency de /diagnose).
    """
    # DA-50: efeito SQL primeiro e best-effort (record_verification nunca
    # levanta). Precisa vir antes do 404 do grafo para que os tres efeitos
    # sejam de fato independentes: um incidente que existe na tabela mas nao
    # no grafo ainda tem a verificacao persistida, senao o operador perderia
    # o veredito por causa de um backend (Neo4j) que e opcional.
    sql_updated = record_verification(
        incident_id,
        diagnosis_correct=body.correct,
        verified_by=body.verified_by,
        verified_root_cause=body.root_cause,
    )

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
    if settings.langfuse_configured and body.trace_id and body.correct is not None:
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

    # DA-50: sem este passo, verified_at/diagnosis_correct ficavam sempre
    # NULL no banco analitico e a tela /admin/incidents + os dashboards nunca
    # mostravam verificacao.
    if not graph_updated and not langfuse_scored and not sql_updated:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Nada para registrar: GraphRAG esta desligado "
                "(GRAPH_RAG_ENABLED=false), nao ha DATABASE_URL ou o "
                "incidente nao existe na tabela `incidents`, e nenhum "
                "trace_id/correct valido foi informado para gravar feedback "
                "no Langfuse."
            ),
        )

    return {
        "incident_id": incident_id,
        "status": "verified",
        "graph_updated": graph_updated,
        "langfuse_scored": langfuse_scored,
        "sql_updated": sql_updated,
    }


@app.get("/llm/policy", dependencies=[Depends(verify_session_or_api_key)])
def llm_policy() -> dict:
    """DA-43: policy de soberanca de dados do AI Gateway, em vigor agora.

    Superficie de auditoria para questionario de seguranca de cliente:
    mostra, por provider, a ORIGIN real resolvida, se pode receber dado
    'public' e 'confidential', e o motivo de cada decisao. Nao inclui
    nenhuma credencial (ver normalize_origin, que descarta userinfo).

    Leitura de config, sem efeito colateral: nao abre circuito, nao chama
    LLM, nao cobra. Autenticacao: X-API-Key (mesma dependency de
    /diagnose).
    """
    return describe_effective_policy()


@app.get("/.well-known/agent-card.json")
def agent_card() -> dict:
    return get_agent_card()


app.include_router(auth_router)  # DA-54: /auth/login, /auth/logout, /auth/session
app.include_router(a2a_router)
app.include_router(admin_router)  # DA-46/47/48: /admin/api/* (ADMIN_API_KEY)
app.include_router(admin_ui_router)
# JS/CSS da Admin UI (CSP `script-src 'self'` proibe script inline).
app.mount(
    "/admin/static",
    StaticFiles(directory=str(Path(__file__).parent / "admin" / "static")),
    name="admin-static",
)  # /admin pages (shell Jinja2, dados via API)

# DA-19: servidor MCP montado em /mcp - ver app/mcp/server.py para o
# contrato de ferramentas (diagnose_incident, list_connectors) e a
# justificativa arquitetural completa.
app.mount("/mcp", build_mcp_asgi_app())
