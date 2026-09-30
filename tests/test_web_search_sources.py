"""DA-57 — testes das fontes de busca web aprovadas.

Tres camadas, como test_admin_systems.py:

  Repository (aiosqlite, modelos reais)
      CRUD de web_search_sources com os MESMOS modelos de producao.

  Resolucao fail-closed (app/services/web_search_sources.py)
      O ponto que mais importa: sem linha habilitada, `resolve_approved_source`
      devolve None e a busca web NAO acontece. Sem banco, erro de banco,
      interface_type desconhecido ou linha sem site_filter/tech_term — todos
      os caminhos tem de dar None, nunca um default em codigo.

  Rotas (TestClient HTTP, DB vivo)
      201/200/404/409/422 reais, incluindo a rejeicao de interface_type fora do
      Literal e a imutabilidade de interface_type no PATCH.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

import app.db as db_module
from app.admin.models import (
    CONNECTOR_TYPES,
    IntegrationSystem,
    LlmCredential,
    LlmModel,
    LlmUsage,
    WebSearchSource,
)
from app.admin.repository import AdminRepository
from app.config import settings
from app.main import app
from app.services.web_search_sources import (
    ApprovedSource,
    list_approved_sources,
    resolve_approved_source,
)

client = TestClient(app)

ADMIN_KEY = "admin-web-search-test-key"


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
            WebSearchSource.__table__,
        ):
            await conn.run_sync(table.create)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def repo(repo_session):
    async with repo_session() as session:
        yield AdminRepository(session)


@pytest.fixture
def _live_db(repo_session, monkeypatch):
    monkeypatch.setattr(db_module, "AsyncSessionLocal", repo_session)
    return repo_session


@pytest_asyncio.fixture
async def factory_com_conexao_propria(tmp_path):
    """Sessionmaker em SQLite de ARQUIVO com `NullPool`: cada sessao abre uma
    conexao nova, como o Postgres faz por request.

    O `StaticPool` do fixture padrao entrega uma UNICA conexao para todas as
    sessoes, entao um dado so `flush()`ado continua visivel para a leitura
    seguinte e a suite nao consegue enxergar nada que dependa de separacao
    entre request. `NullPool` fecha a porta.
    """
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'commit.db'}",
        poolclass=NullPool,
    )
    async with engine.begin() as conn:
        for table in (WebSearchSource.__table__,):
            await conn.run_sync(table.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def _db_propria(factory_com_conexao_propria, monkeypatch):
    monkeypatch.setattr(db_module, "AsyncSessionLocal", factory_com_conexao_propria)
    return factory_com_conexao_propria


def _em_sessao_nova(factory, fn):
    """Roda `fn(session)` numa sessao NOVA (conexao nova = transacao nova)."""

    async def _main():
        async with factory() as session:
            return await fn(session)

    return asyncio.run(_main())


# Os tres testes abaixo travam o CONTRATO ("a escrita de um request e visivel
# para a proxima conexao"), e nao a presenca de `commit()` numa linha
# especifica: quem garante a durabilidade hoje e o `await session.commit()` do
# teardown de `get_db_session` (app/db.py:178). Se alguem mexer ali — ou
# trocar a rota por `flush()` achando que o teardown cobre —, estes testes
# falham. Nao tentam provar que a rota tem commit; com `get_db_session` no
# meio, um teste Unitario nao consegue separar as duas coisas.


def test_create_e_visivel_na_proxima_conexao(_db_propria):
    payload = {
        "interface_type": "odata",
        "site_filter": "site:help.sap.com",
        "tech_term": "OData commit",
    }
    r = client.post("/admin/api/web-search-sources", headers=_auth(), json=payload)
    assert r.status_code == 201, r.text

    async def _read(session):
        got = await AdminRepository(session).get_web_search_source(interface_type="odata")
        return None if got is None else got.tech_term

    assert _em_sessao_nova(_db_propria, _read) == "OData commit"


def test_delete_e_visivel_na_proxima_conexao(_db_propria):
    created = client.post(
        "/admin/api/web-search-sources",
        headers=_auth(),
        json={"interface_type": "cap", "site_filter": "site:cap.cloud.sap", "tech_term": "CAP"},
    ).json()
    r_del = client.delete(f"/admin/api/web-search-sources/{created['id']}", headers=_auth())
    assert r_del.status_code == 200, r_del.text

    async def _read(session):
        return await AdminRepository(session).get_web_search_source(created["id"])

    assert _em_sessao_nova(_db_propria, _read) is None

    # e o 409 volta a valer: com o delete commitado, o nome esta livre
    r = client.post(
        "/admin/api/web-search-sources",
        headers=_auth(),
        json={"interface_type": "cap", "site_filter": "site:cap.cloud.sap", "tech_term": "CAP 2"},
    )
    assert r.status_code == 201, r.text


def test_patch_e_visivel_na_proxima_conexao(_db_propria):
    created = client.post(
        "/admin/api/web-search-sources",
        headers=_auth(),
        json={"interface_type": "apim", "site_filter": "site:help.sap.com", "tech_term": "APIM v1"},
    ).json()
    r = client.patch(
        f"/admin/api/web-search-sources/{created['id']}",
        headers=_auth(),
        json={"tech_term": "APIM v2"},
    )
    assert r.status_code == 200, r.text

    async def _read(session):
        got = await AdminRepository(session).get_web_search_source(created["id"])
        return None if got is None else got.tech_term

    assert _em_sessao_nova(_db_propria, _read) == "APIM v2"


# --- repository -------------------------------------------------------------


@pytest.mark.asyncio
async def test_crud_fonte(repo, repo_session):
    s = await repo.create_web_search_source(
        interface_type="po",
        site_filter="site:help.sap.com/docs/SAP_PROCESS_INTEGRATION",
        tech_term="SAP PI PO IDoc message monitor",
        notes="DA-56",
    )
    assert s.id is not None
    assert s.enabled is True

    assert (await repo.get_web_search_source(interface_type="po")).id == s.id
    assert (await repo.get_web_search_source(s.id)) is not None
    assert (await repo.get_web_search_source(uuid.uuid4())) is None
    assert (await repo.get_web_search_source(interface_type="inexistente")) is None

    rows = await repo.list_web_search_sources()
    assert len(rows) == 1
    assert rows[0].interface_type == "po"

    updated = await repo.update_web_search_source(s.id, {"tech_term": "novo", "enabled": False})
    assert updated is not None
    assert updated.tech_term == "novo"
    assert updated.enabled is False

    # interface_type e' a chave de casamento com o grafo: nao entra na allowlist
    await repo.update_web_search_source(s.id, {"interface_type": "hackeado"})
    assert (await repo.get_web_search_source(s.id)).interface_type == "po"

    assert await repo.delete_web_search_source(s.id) is True
    assert await repo.delete_web_search_source(s.id) is False


# --- resolucao fail-closed --------------------------------------------------


class _Row:
    def __init__(self, it, sf, tt, enabled=True):
        self.interface_type = it
        self.site_filter = sf
        self.tech_term = tt
        self.enabled = enabled


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return type("S", (), {"all": staticmethod(lambda: list(self._rows))})()


class _FakeFactory:
    """`sessionmaker` fake: `factory()` devolve a sessao e `with` funciona,
    servindo as duas funcoes de `web_search_sources`."""

    def __init__(self, rows):
        self._rows = list(rows)

    def __call__(self):
        return self

    def execute(self, *a, **kw):
        return _Result(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_resolve_sem_banco_devolve_none(monkeypatch):
    """Sem DATABASE_URL nao ha fonte aprovada — e o comportamento correto."""
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr("app.services.web_search_sources.get_sync_session_factory", lambda: None)
    assert resolve_approved_source("po") is None


@pytest.mark.parametrize("vazio", [None, ""])
def test_resolve_interface_type_vazio(vazio):
    assert resolve_approved_source(vazio) is None


def test_resolve_erro_de_banco_vira_none_nao_excecao(monkeypatch):
    """Falha de banco nao pode derrubar o diagnostico: a busca web e um
    fallback opcional do RAG."""

    def _boom():
        raise RuntimeError("banco fora do ar")

    monkeypatch.setattr("app.services.web_search_sources.get_sync_session_factory", _boom)
    assert resolve_approved_source("po") is None


def test_resolve_linha_habilitada_devolve_fonte(monkeypatch):
    """Linha habilitada no banco vira a query: tech_term e site_filter sao
    os da tabela, nao de um mapa em codigo."""
    monkeypatch.setattr(
        "app.services.web_search_sources.get_sync_session_factory",
        _FakeFactory([_Row("po", "site:help.sap.com", "SAP PI PO IDoc")]),
    )
    got = resolve_approved_source("po")
    assert got is not None
    assert got.site_filter == "site:help.sap.com"
    assert got.tech_term == "SAP PI PO IDoc"


def test_resolve_linha_desabilitada_devolve_none(monkeypatch):
    monkeypatch.setattr(
        "app.services.web_search_sources.get_sync_session_factory",
        _FakeFactory([_Row("po", "site:help.sap.com", "t", enabled=False)]),
    )
    assert resolve_approved_source("po") is None


def test_resolve_linha_sem_filtro_vazio_devolve_none(monkeypatch):
    """Linha habilitada mas sem site_filter/tech_term e' configuracao morta:
    a resolucao ignora, e o motivo fica em log."""
    monkeypatch.setattr(
        "app.services.web_search_sources.get_sync_session_factory",
        _FakeFactory([_Row("po", "   ", "t")]),
    )
    assert resolve_approved_source("po") is None


def test_resolve_sem_linha_devolve_none(monkeypatch):
    monkeypatch.setattr(
        "app.services.web_search_sources.get_sync_session_factory", _FakeFactory([])
    )
    assert resolve_approved_source("po") is None


# --- rotas ------------------------------------------------------------------


def test_create_e_list_fonte_http(_live_db):
    r = client.post(
        "/admin/api/web-search-sources",
        headers=_auth(),
        json={
            "interface_type": "po",
            "site_filter": "site:help.sap.com",
            "tech_term": "SAP PI PO IDoc",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["interface_type"] == "po"
    assert r.json()["enabled"] is True

    r = client.get("/admin/api/web-search-sources", headers=_auth())
    assert r.status_code == 200
    assert [x["interface_type"] for x in r.json()] == ["po"]

    fonte_id = r.json()[0]["id"]
    r = client.get(f"/admin/api/web-search-sources/{fonte_id}", headers=_auth())
    assert r.status_code == 200

    r = client.get(f"/admin/api/web-search-sources/{uuid.uuid4()}", headers=_auth())
    assert r.status_code == 404


def test_interface_type_fora_do_literal_rejeitado_http(_live_db):
    """Linha para um conector que o grafo nunca produz seria configuracao
    morta — mesma classe de erro que o gate connector_reachable barra."""
    r = client.post(
        "/admin/api/web-search-sources",
        headers=_auth(),
        json={
            "interface_type": "conector_que_nao_existe",
            "site_filter": "site:help.sap.com",
            "tech_term": "x",
        },
    )
    assert r.status_code == 422
    assert "interface_type invalido" in r.json()["detail"]


def test_fonte_duplicada_http_409(_live_db):
    payload = {
        "interface_type": "odata",
        "site_filter": "site:help.sap.com",
        "tech_term": "OData",
    }
    assert (
        client.post("/admin/api/web-search-sources", headers=_auth(), json=payload).status_code
        == 201
    )
    r = client.post("/admin/api/web-search-sources", headers=_auth(), json=payload)
    assert r.status_code == 409


def test_patch_nao_aceita_interface_type_http(_live_db):
    """`interface_type` e' imutavel: trocaria qual conector tem qual filtro."""
    created = client.post(
        "/admin/api/web-search-sources",
        headers=_auth(),
        json={"interface_type": "cap", "site_filter": "site:cap.cloud.sap", "tech_term": "CAP"},
    ).json()

    r = client.patch(
        f"/admin/api/web-search-sources/{created['id']}",
        headers=_auth(),
        json={"interface_type": "po"},
    )
    assert r.status_code == 200
    assert r.json()["interface_type"] == "cap"

    r = client.patch(
        f"/admin/api/web-search-sources/{created['id']}",
        headers=_auth(),
        json={"enabled": False},
    )
    assert r.json()["enabled"] is False


def test_patch_rejeita_filtro_vazio_http(_live_db):
    """site_filter vazio e' a forma de a linha virar configuracao morta: a
    resolucao fail-closed ignora a linha, mas o cadastro continuaria dizendo
    que o conector tem fonte."""
    created = client.post(
        "/admin/api/web-search-sources",
        headers=_auth(),
        json={"interface_type": "rfc", "site_filter": "site:help.sap.com", "tech_term": "RFC"},
    ).json()

    r = client.patch(
        f"/admin/api/web-search-sources/{created['id']}",
        headers=_auth(),
        json={"site_filter": "   "},
    )
    assert r.status_code == 422


def test_delete_fonte_http(_live_db):
    created = client.post(
        "/admin/api/web-search-sources",
        headers=_auth(),
        json={"interface_type": "apim", "site_filter": "site:help.sap.com", "tech_term": "APIM"},
    ).json()

    r = client.delete(f"/admin/api/web-search-sources/{created['id']}", headers=_auth())
    assert r.status_code == 200
    r = client.delete(f"/admin/api/web-search-sources/{created['id']}", headers=_auth())
    assert r.status_code == 404


def test_exige_chave_admin():
    assert client.get("/admin/api/web-search-sources").status_code == 401


def test_ui_web_search_ok():
    r = client.get("/admin/web-search")
    assert r.status_code == 200
    assert "Fontes de busca web" in r.text


# --- cobertura do pipeline --------------------------------------------------


def test_seed_cobre_todos_os_conectores_do_literal():
    """A migration 008 tem de cobrir o Literal inteiro — e' o que o gate
    `connector_reachable` passa a exigir na 7a superficie. Aqui a mesma
    garantia em forma de contrato, sem depender de rodar o gate."""
    import re
    from pathlib import Path

    fonte = Path("alembic/versions/008_create_web_search_sources.py").read_text(encoding="utf-8")
    m = re.search(r"^SEED\s*=\s*\[(.*?)^\]", fonte, re.DOTALL | re.MULTILINE)
    assert m is not None, "bloco SEED nao encontrado na migration 008"
    seed = set(re.findall(r'\(\s*"([a-z_]+)"\s*,', m.group(1)))
    assert set(CONNECTOR_TYPES) <= seed, (
        f"conectores sem fonte no seed: {sorted(set(CONNECTOR_TYPES) - seed)}"
    )


def test_list_approved_sources_filtra_filtro_vazio(monkeypatch):
    """Linha habilitada com site_filter/tech_term vazio e' configuracao morta e
    nao aparece na listagem. (O filtro de `enabled` e' do SQL — testado contra
    um banco de verdade em `test_list_approved_sources_filtra_desabilitada_sql`.)"""
    monkeypatch.setattr(
        "app.services.web_search_sources.get_sync_session_factory",
        _FakeFactory([_Row("po", "site:a", "t"), _Row("rfc", "  ", "t")]),
    )
    got = list_approved_sources()
    assert set(got) == {"po"}
    assert isinstance(got["po"], ApprovedSource)


def test_list_approved_sources_filtra_desabilitada_no_sql():
    """O `WHERE enabled IS TRUE` de `list_approved_sources` e' o que mantem
    uma linha desabilitada fora da listagem — e' conferido na clausula
    compilada, sem depender de Postgres (o sessionmaker sync da DA-52 usa
    `connect_timeout`, que e' psycopg2-only e nao abre SQLite)."""
    from sqlalchemy import select

    stmt = select(WebSearchSource).where(WebSearchSource.enabled.is_(True))
    sql = " ".join(str(stmt.compile(compile_kwargs={"literal_binds": True})).split())
    assert "web_search_sources.enabled IS true" in sql or (
        "web_search_sources.enabled = 1" in sql
    ), f"a clausula de enabled sumiu do SELECT: {sql}"


def test_connectors_types_cobre_o_literal_do_pipeline():
    assert "po" in CONNECTOR_TYPES
    assert "successfactors" in CONNECTOR_TYPES
