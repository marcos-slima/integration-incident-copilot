"""SAP Integration Copilot - entrypoint FastAPI."""

import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from langfuse import get_client
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.a2a.agent_card import get_agent_card
from app.a2a.server import router as a2a_router
from app.agent.graph import run_diagnosis
from app.config import settings
from app.mcp.server import build_mcp_asgi_app
from app.mcp.server import mcp as mcp_server
from app.models import DiagnosisResponse, IncidentRequest

logger = logging.getLogger(__name__)

# ─── Rate limiting ─────────────────────────────────────────────────────────
# Limite por IP: 10 diagnosticos por minuto (configuravel via RATE_LIMIT no .env)
limiter = Limiter(key_func=get_remote_address, default_limits=["10/minute"])

# ─── API Key (opcional) ────────────────────────────────────────────────────
# Se API_KEY nao estiver configurado no .env, autenticacao e desabilitada.
# Para habilitar: API_KEY=sua-chave-secreta no .env
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


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
    chave estavel, configure API_KEY/A2A_API_KEY no .env."""
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_api_keys_configured()
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


@app.get("/.well-known/agent-card.json")
def agent_card() -> dict:
    return get_agent_card()


app.include_router(a2a_router)

# DA-19: servidor MCP montado em /mcp - ver app/mcp/server.py para o
# contrato de ferramentas (diagnose_incident, list_connectors) e a
# justificativa arquitetural completa.
app.mount("/mcp", build_mcp_asgi_app())
