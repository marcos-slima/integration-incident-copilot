"""UI admin (DA-46/47/48) — paginas Jinja2 finas, sem dado protegido.

As paginas servidas por /admin/* sao shells navegaveis: formularios e
tabelas vazios. TODO dado chega via fetch a /admin/api/*, que exige a
ADMIN_API_KEY (header X-API-Admin-Key). A chave fica no sessionStorage do
browser do operador (nunca em cookie nem em URL) e e anexada a cada chamada.

Isso preserva a auth nas OPERACOES de dados ao mesmo tempo que mantem a
UI navegavel (uma pagina de formulario nao contem segredo nenhum).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app.config import settings

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

ui_router = APIRouter(prefix="/admin")


def _ctx() -> dict:
    return {
        "managed": settings.llm_registry_db,
        "db_configured": bool(settings.database_url),
        "metering_enabled": settings.metering_enabled,
    }


def _render(request: Request, name: str) -> object:
    # Starlette moderno: TemplateResponse(request, name, context)
    return templates.TemplateResponse(request, name, {**_ctx()})


@ui_router.get("", include_in_schema=False)
async def admin_index(request: Request):
    return _render(request, "index.html")


@ui_router.get("/models", include_in_schema=False)
async def admin_models(request: Request):
    return _render(request, "models.html")


@ui_router.get("/usage", include_in_schema=False)
async def admin_usage(request: Request):
    return _render(request, "usage.html")


@ui_router.get("/systems", include_in_schema=False)
async def admin_systems(request: Request):
    return _render(request, "systems.html")


@ui_router.get("/incidents", include_in_schema=False)
async def admin_incidents(request: Request):
    return _render(request, "incidents.html")
