"""SAP Integration Copilot - entrypoint FastAPI."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from langfuse import get_client

from app.a2a.agent_card import get_agent_card
from app.a2a.server import router as a2a_router
from app.agent.graph import run_diagnosis
from app.models import DiagnosisResponse, IncidentRequest


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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/diagnose", response_model=DiagnosisResponse)
def diagnose(request: IncidentRequest) -> DiagnosisResponse:
    return run_diagnosis(request)


# Camada A2A (Agent2Agent) - endpoint em paralelo ao /diagnose, mesma
# orquestracao por tras. Ver docs/proposals/a2a-interoperability-layer.md
# e app/a2a/.
@app.get("/.well-known/agent-card.json")
def agent_card() -> dict:
    return get_agent_card()


app.include_router(a2a_router)
