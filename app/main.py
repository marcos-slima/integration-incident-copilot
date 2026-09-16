"""SAP Integration Copilot - entrypoint FastAPI."""

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
from app.models import DiagnosisResponse, IncidentRequest

# ─── Rate limiting ─────────────────────────────────────────────────────────
# Limite por IP: 10 diagnosticos por minuto (configuravel via RATE_LIMIT no .env)
limiter = Limiter(key_func=get_remote_address, default_limits=["10/minute"])

# ─── API Key (opcional) ────────────────────────────────────────────────────
# Se API_KEY nao estiver configurado no .env, autenticacao e desabilitada.
# Para habilitar: API_KEY=sua-chave-secreta no .env
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: str | None = Security(api_key_header)) -> None:
    """Valida API Key se configurada. Se nao configurada, permite tudo."""
    configured_key = getattr(settings, "api_key", None)
    if not configured_key:
        return  # API Key nao configurada — modo aberto
    if api_key != configured_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key invalida ou ausente",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
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
