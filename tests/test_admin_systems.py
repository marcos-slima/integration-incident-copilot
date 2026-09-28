"""DA-49 (Fase B) — testes do catalogo de sistemas integrados.

Duas camadas, mimetizando a estrategia da Fase A:

  Repository (aiosqlite, modelos reais)
      CRUD de integration_systems reutilizando os MESMOS modelos de
      producao (tipos compativeis com SQLite), igual test_admin_repository.

  Rotas (TestClient HTTP, DB vivo)
      Em vez de so o 503 sem banco (coberto em test_admin_routes.py),
      aqui monkeypatchamos app.db.AsyncSessionLocal com um sessionmaker
      aiosqlite em memoria para exercitar o CRUD completo por HTTP:
      201/200/404/409/422 reais, validacao de connector/ambiente/status e
      contagem em /admin/api/registry/status.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db as db_module
from app.admin.models import IntegrationSystem, LlmCredential, LlmModel, LlmUsage
from app.admin.repository import AdminRepository
from app.config import settings
from app.main import app

client = TestClient(app)

ADMIN_KEY = "admin-systems-test-key"


@pytest.fixture(autouse=True)
def _fixed_admin_key(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", ADMIN_KEY)


def _auth() -> dict:
    return {"X-API-Admin-Key": ADMIN_KEY}


@pytest_asyncio.fixture
async def repo_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        for table in (
            LlmModel.__table__,
            LlmCredential.__table__,
            LlmUsage.__table__,
            IntegrationSystem.__table__,
        ):
            await conn.run_sync(table.create)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def repo(repo_session):
    async with repo_session() as session:
        yield AdminRepository(session)


# --- repository -------------------------------------------------------------


@pytest.mark.asyncio
async def test_crud_sistema(repo, repo_session):
    s = await repo.create_system(
        system_key="sap_odata_prod",
        name="SAP OData — Producao",
        vendor="SAP",
        connector_type="odata",
        base_url="https://s4h.example.com/sap/opu/odata",
        environment="prod",
        notes="gw principal",
    )
    assert s.id is not None
    assert s.status == "active"

    assert (await repo.get_system(key="sap_odata_prod")).id == s.id
    assert (await repo.get_system(s.id)) is not None
    assert (await repo.get_system(uuid.uuid4())) is None
    assert (await repo.get_system(key="nao_existe")) is None

    rows = await repo.list_systems()
    assert len(rows) == 1
    assert rows[0].connector_type == "odata"

    updated = await repo.update_system(s.id, {"status": "degraded", "base_url": "", "name": "Novo"})
    assert updated is not None
    assert updated.status == "degraded"
    assert updated.base_url is None
    assert updated.name == "Novo"
    # fora da allowlist nao muda
    await repo.update_system(s.id, {"system_key": "hackeado"})
    assert (await repo.get_system(s.id)).system_key == "sap_odata_prod"

    assert await repo.delete_system(s.id) is True
    assert await repo.get_system(s.id) is None
    assert await repo.delete_system(s.id) is False


@pytest.mark.asyncio
async def test_sistema_duplicado_db_levanta_integrity_error(repo):
    await repo.create_system(system_key="sap_rfc", name="RFC", vendor="SAP", connector_type="rfc")
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await repo.create_system(
            system_key="sap_rfc", name="Duplicate", vendor="SAP", connector_type="rfc"
        )


@pytest.mark.asyncio
async def test_sistema_defaults_de_ambiente_e_status(repo):
    s = await repo.create_system(
        system_key="cap_dev",
        name="CAP BTP",
        vendor="SAP",
        connector_type="cap",
        environment="dev",
    )
    assert s.environment == "dev"
    assert s.status == "active"
    assert s.base_url is None


# --- rotas (DB vivo) --------------------------------------------------------


@pytest.fixture
def _live_db(repo_session, monkeypatch):
    monkeypatch.setattr(db_module, "AsyncSessionLocal", repo_session)
    return repo_session


def test_create_e_list_sistema_http(_live_db):
    r = client.post(
        "/admin/api/systems",
        headers=_auth(),
        json={
            "system_key": "servicenow_eu",
            "name": "ServiceNow EU",
            "vendor": "ServiceNow",
            "connector_type": "servicenow",
            "environment": "prod",
            "status": "active",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["system_key"] == "servicenow_eu"
    assert body["environment"] == "prod"

    listed = client.get("/admin/api/systems", headers=_auth())
    assert listed.status_code == 200
    assert [s["system_key"] for s in listed.json()] == ["servicenow_eu"]

    got = client.get(f"/admin/api/systems/{body['id']}", headers=_auth())
    assert got.status_code == 200
    assert got.json()["vendor"] == "ServiceNow"


def test_validacao_conector_ambiente_status_http(_live_db):
    base = {
        "system_key": "salesforce_prod",
        "name": "Salesforce Produção",
        "vendor": "Salesforce",
    }
    r = client.post("/admin/api/systems", headers=_auth(), json={**base, "connector_type": "soap"})
    assert r.status_code == 422
    assert "connector_type invalido" in r.json()["detail"]

    r = client.post(
        "/admin/api/systems",
        headers=_auth(),
        json={**base, "connector_type": "salesforce", "environment": "lua"},
    )
    assert r.status_code == 422
    assert "environment invalido" in r.json()["detail"]

    r = client.post(
        "/admin/api/systems",
        headers=_auth(),
        json={**base, "connector_type": "salesforce", "status": "queimado"},
    )
    assert r.status_code == 422
    assert "status invalido" in r.json()["detail"]


def test_sistema_duplicado_http_409(_live_db):
    payload = {
        "system_key": "ariba_qa",
        "name": "Ariba QA",
        "vendor": "SAP",
        "connector_type": "ariba",
        "environment": "stage",
    }
    assert client.post("/admin/api/systems", headers=_auth(), json=payload).status_code == 201
    r = client.post("/admin/api/systems", headers=_auth(), json=payload)
    assert r.status_code == 409
    assert "ja registrado" in r.json()["detail"]


def test_patch_sistema_http(_live_db):
    created = client.post(
        "/admin/api/systems",
        headers=_auth(),
        json={
            "system_key": "workday_prod",
            "name": "Workday",
            "vendor": "Workday",
            "connector_type": "workday",
            "status": "active",
        },
    ).json()
    sid = created["id"]

    r = client.patch(
        f"/admin/api/systems/{sid}",
        headers=_auth(),
        json={"status": "degraded", "environment": "stage"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
    assert r.json()["environment"] == "stage"

    r = client.patch(f"/admin/api/systems/{sid}", headers=_auth(), json={"status": "explodiu"})
    assert r.status_code == 422
    assert "status invalido" in r.json()["detail"]

    assert (
        client.patch(
            f"/admin/api/systems/{uuid.uuid4()}", headers=_auth(), json={"status": "offline"}
        ).status_code
        == 404
    )


def test_delete_sistema_http(_live_db):
    created = client.post(
        "/admin/api/systems",
        headers=_auth(),
        json={"system_key": "sap_apim", "name": "APIM", "vendor": "SAP", "connector_type": "apim"},
    ).json()
    sid = created["id"]

    r = client.delete(f"/admin/api/systems/{sid}", headers=_auth())
    assert r.status_code == 200
    assert client.get(f"/admin/api/systems/{sid}", headers=_auth()).status_code == 404
    assert client.delete(f"/admin/api/systems/{uuid.uuid4()}", headers=_auth()).status_code == 404


def test_registry_status_conta_sistemas(_live_db):
    client.post(
        "/admin/api/systems",
        headers=_auth(),
        json={"system_key": "cap_prod", "name": "CAP", "vendor": "SAP", "connector_type": "cap"},
    )
    r = client.get("/admin/api/registry/status", headers=_auth())
    assert r.status_code == 200
    assert r.json()["systems_count"] == 1


def test_ui_systems_ok():
    r = client.get("/admin/systems")
    assert r.status_code == 200
    assert "Sistemas integrados" in r.text
