"""DA-50 (Fase C) - incidentes correlacionados com o catalogo de sistemas.

Quatro camadas, mesma estrategia de tests/test_admin_systems.py:

  1. Correlacao (funcoes puras, sem I/O) - app/admin/correlation.py
     Regra: match exato por `system_key` > fallback por `connector_type`
     unico > ambiguo (fail-closed) > sem match.
  2. Repositorio + rotas (aiosqlite in-memory, modelos REAIS de admin +
     espelho SQLite do Incident) - importa a classe de PRODUCAO
     `IncidentRepository`: o espelho de tests/test_incident_repository.py
     reimplementa a classe, entao nao cobriria os filtros novos.
  3. record_verification (sessionmaker SQLite sincrono) e a propagacao
     request -> CopilotState -> incidents.connector_source_system.
  4. scripts/validate_dashboards.py - normalizacao das macros do Grafana e
     regressao dos dois bugs de SQL ja corrigidos (evidence_strength FLOAT,
     ROUND(double, int)).

O que este arquivo protege de proposito (regressoes reais ja observadas):
  - `correct=None` NAO pode virar `True` (inflaria a taxa de acuracia);
  - o SQL do verify roda mesmo quando o grafo responde 404 (efeitos
    independentes, nao "o primeiro efecto manda");
  - o drill-down por sistema nao pode engolir outro ambiente que usa o
    mesmo conector (indice de correlacao montado com o catalogo inteiro).
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON as SA_JSON
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, Uuid, create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import app.db as db_module
import app.services.incident_repository as incident_repo_module
from app.admin.correlation import (
    MATCH_AMBIGUOUS,
    MATCH_CONNECTOR_TYPE,
    MATCH_NONE,
    MATCH_SYSTEM_KEY,
    build_system_index,
    correlate,
)
from app.admin.models import IntegrationSystem, LlmCredential, LlmModel, LlmUsage
from app.config import Settings, settings
from app.main import app
from app.services import incident_recorder
from app.services.incident_repository import IncidentRepository

client = TestClient(app)

ADMIN_KEY = "admin-da50-test-key"

_DASHBOARD_DIR = (
    pathlib.Path(__file__).resolve().parent.parent / "deploy" / "grafana" / "dashboards"
)


@pytest.fixture(autouse=True)
def _fixed_admin_key(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", ADMIN_KEY)


def _auth() -> dict:
    return {"X-API-Admin-Key": ADMIN_KEY}


# ---------------------------------------------------------------------------
# Espelho SQLite do Incident (JSONB/UUID do Postgres -> JSON/String)
# ---------------------------------------------------------------------------


class SqliteBase(DeclarativeBase):
    pass


class IncidentSQLite(SqliteBase):
    """Espelho fiel do modelo de producao, compativel com SQLite.

    NAO reaproveita o espelho de tests/test_incident_repository.py: aqui
    `evidence_strength` e Float como na migracao 002 (o outro ainda diz
    String(32), pre-migration) e o `id` e `Uuid(as_uuid=True)` como na
    migracao 001, porque get_by_id() normaliza a string da URL para
    `uuid.UUID` antes do WHERE - com String(36) o bind do UUID nao casa e o
    detalhe do incidente daria 404 num banco de teste.
    """

    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    interface_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    connector_source_system: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    probable_root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    diagnosis_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_strength: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_provider_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_domain: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_json: Mapped[Any | None] = mapped_column(SA_JSON, nullable=True)
    sensitivity_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pii_detected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    redaction_applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_codes: Mapped[Any | None] = mapped_column(SA_JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    diagnosis_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verified_root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)


@dataclasses.dataclass
class _Db:
    """Handle da fixture: caminho do arquivo e como popular o catalogo."""

    path: pathlib.Path
    session: sessionmaker


def _open_db(tmp_path: pathlib.Path, monkeypatch) -> _Db:
    """Sema de arquivo SQLite (nao :memory:) + engine async apontado para
    app.db.AsyncSessionLocal.

    Arquivo em vez de in-memory porque o seed e as requisicoes HTTP rodam em
    event loops DIFERENTES (o do pytest-asyncio e o do TestClient): com
    :memory: + StaticPool a mesma conexao aiosqlite seria compartilhada entre
    loops, o que o driver nao suporta. Com arquivo, cada loop abre a sua
    conexao e ambas enxergam os mesmos dados.
    """
    db_path = tmp_path / "da50.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    for table in (
        LlmModel.__table__,
        LlmCredential.__table__,
        LlmUsage.__table__,
        IntegrationSystem.__table__,
    ):
        table.create(sync_engine, checkfirst=True)
    IncidentSQLite.__table__.create(sync_engine, checkfirst=True)

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", factory)
    monkeypatch.setattr(incident_repo_module, "Incident", IncidentSQLite)
    return _Db(path=db_path, session=sessionmaker(sync_engine, expire_on_commit=False))


def _seed(db: _Db, systems: list[dict[str, Any]], incidents: list[dict[str, Any]]) -> None:
    with db.session() as session:
        for spec in systems:
            session.add(
                IntegrationSystem(
                    system_key=spec["system_key"],
                    name=spec["name"],
                    vendor=spec.get("vendor"),
                    connector_type=spec["connector_type"],
                    base_url=spec.get("base_url"),
                    environment=spec.get("environment") or "prod",
                    status=spec.get("status") or "active",
                    notes=spec.get("notes"),
                )
            )
        for row in incidents:
            session.add(IncidentSQLite(**row))
        session.commit()


@pytest.fixture
def sqlite_db(tmp_path, monkeypatch):
    return _open_db(tmp_path, monkeypatch)


@pytest.fixture
def _no_db(monkeypatch):
    """Sem banco: os endpoints de dados de incidentes devem ser 503."""
    monkeypatch.setattr(db_module, "AsyncSessionLocal", None)


# ---------------------------------------------------------------------------
# 1. Correlacao (puro)
# ---------------------------------------------------------------------------


def _systems() -> list[dict[str, Any]]:
    """Catalogo minimo: dois ambientes do mesmo conector (CAP) e um
    conector unico (OData)."""
    return [
        {
            "system_key": "cap_prod",
            "name": "CAP Producao",
            "vendor": "SAP",
            "connector_type": "cap",
            "environment": "prod",
            "status": "active",
        },
        {
            "system_key": "cap_stage",
            "name": "CAP Stage",
            "vendor": "SAP",
            "connector_type": "cap",
            "environment": "stage",
            "status": "degraded",
        },
        {
            "system_key": "sap_odata_prod",
            "name": "S/4HANA OData",
            "vendor": "SAP",
            "connector_type": "odata",
            "environment": "prod",
            "status": "active",
        },
    ]


def test_match_exato_por_system_key():
    index = build_system_index(_systems())
    result = correlate("cap", "cap_stage", index)
    assert result["match"] == MATCH_SYSTEM_KEY
    assert result["system_key"] == "cap_stage"
    assert result["status"] == "degraded"
    assert result["environment"] == "stage"


def test_match_exato_tem_precedencia_sobre_o_fallback_por_conector():
    """Mesmo com dois CAP no catalogo, o system_key informado decide: se o
    fallback vencesse, todo CAP viraria 'ambiguo' e o operador perderia a
    correlacao que o cliente ja deu de bandeja."""
    index = build_system_index(_systems())
    result = correlate("cap", "cap_prod", index)
    assert result["match"] == MATCH_SYSTEM_KEY
    assert result["system_key"] == "cap_prod"


def test_fallback_por_connector_type_quando_so_ha_um_candidato():
    index = build_system_index(_systems())
    result = correlate("odata", "rotulo-antigo-do-conector", index)
    assert result["match"] == MATCH_CONNECTOR_TYPE
    assert result["system_key"] == "sap_odata_prod"


def test_fallback_ambiguo_nao_escolhe_um_sistema():
    """Dois CAP no catalogo e incidente sem system_key: fail-closed, devolve
    os candidatos em vez de chutar (mesmo principio da Capability Registry,
    DA-27)."""
    index = build_system_index(_systems())
    result = correlate("cap", None, index)
    assert result["match"] == MATCH_AMBIGUOUS
    assert result["system_key"] is None
    assert result["candidates"] == ["cap_prod", "cap_stage"]


def test_system_key_desconhecido_cai_para_o_conector():
    """system_key que nao esta no catalogo nao pode 'criar' um sistema: o
    fallback por conector ainda resolve, e se nao houver candidato unico,
    fica ambiguo/sem match."""
    index = build_system_index(_systems())
    assert correlate("odata", "sistema_renomeado", index)["system_key"] == "sap_odata_prod"
    assert correlate("cap", "cap_desativado", index)["match"] == MATCH_AMBIGUOUS
    assert correlate("inexistente", "nao_cadastrado", index)["match"] == MATCH_NONE


def test_sem_interface_type_so_a_regra_do_system_key_se_aplica():
    index = build_system_index(_systems())
    assert correlate(None, "sap_odata_prod", index)["match"] == MATCH_SYSTEM_KEY
    assert correlate(None, "OData", index)["match"] == MATCH_NONE


def test_source_com_espacos_e_indice_com_dicionarios():
    index = build_system_index(_systems())
    assert correlate("odata", "  sap_odata_prod  ", index)["match"] == MATCH_SYSTEM_KEY
    assert set(index["by_key"]) == {"cap_prod", "cap_stage", "sap_odata_prod"}
    assert sorted(index["by_connector_type"]) == ["cap", "odata"]


def test_indice_ignora_campos_vazios():
    index = build_system_index([{"system_key": "x", "connector_type": ""}, {"system_key": ""}])
    assert index["by_key"] == {"x": index["by_key"]["x"]}
    assert index["by_connector_type"] == {}


def test_correlacao_aceita_modelos_orm_como_objects():
    class _System:
        system_key = "servicenow_eu"
        name = "ServiceNow EU"
        vendor = "ServiceNow"
        connector_type = "servicenow"
        environment = "prod"
        status = "active"

    index = build_system_index([_System()])
    result = correlate("servicenow", "servicenow_eu", index)
    assert result["match"] == MATCH_SYSTEM_KEY
    assert result["vendor"] == "ServiceNow"


# ---------------------------------------------------------------------------
# 2. IncidentRepository de producao (filtros novos)
# ---------------------------------------------------------------------------


def _incident(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "interface_type": "cap",
        "description": "IDoc 51 travado",
        "connector_source_system": "cap_prod",
        "is_mock": True,
        "probable_root_cause": "material lock",
        "model_confidence": 0.8,
        "diagnosis_confidence": 0.7,
        "evidence_strength": 0.85,
        "llm_provider_used": "ollama",
        "agent_domain": "sap",
        "evidence_json": [{"trust_level": "system_observed"}],
        "sensitivity_level": "internal",
        "pii_detected": False,
        "redaction_applied": False,
        "latency_ms": 1200,
        "error_codes": ["IDOC_51"],
        "created_at": now,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_list_recent_filtros(sqlite_db):
    antigo = _incident(
        interface_type="odata",
        connector_source_system="sap_odata_prod",
        created_at=datetime.now(UTC) - timedelta(days=1),
    )
    pendente = _incident()
    verificado = _incident(diagnosis_correct=True, verified_at=datetime.now(UTC))
    # verificado SEM veredito: `correct` ausente deixa diagnosis_correct NULL,
    # entao continua contando como pendente de veredito.
    sem_veredito = _incident(verified_at=datetime.now(UTC))
    _seed(sqlite_db, [], [antigo, pendente, verificado, sem_veredito])

    engine = create_async_engine(f"sqlite+aiosqlite:///{sqlite_db.path}")
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            repo = IncidentRepository(session)
            assert len(await repo.list_recent(50)) == 4
            assert len(await repo.list_recent(50, interface_type="odata")) == 1
            assert len(await repo.list_recent(50, source_system="cap_prod")) == 3
            # system_key exato: nao faz match por conector nem por rotulo
            assert len(await repo.list_recent(50, source_system="cap_prod ")) == 3
            assert len(await repo.list_recent(50, source_system="cap")) == 0
            # verified=True = tem veredito; verified=False = sem veredito
            assert len(await repo.list_recent(50, verified=True)) == 1
            assert len(await repo.list_recent(50, verified=False)) == 3

            assert await repo.count() == 4
            assert await repo.count(verified=True) == 1
            assert await repo.count(verified=False) == 3

            # ordenacao por created_at desc + limit
            recentes = await repo.list_recent(2)
            assert len(recentes) == 2
            assert recentes[0].created_at >= recentes[1].created_at
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# 3. Rotas /admin/api/incidents
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded(sqlite_db):
    """Dois CAP no catalogo + um OData, e tres incidentes: um com system_key
    exato de prod, um de stage, e um legado (rotulo do conector)."""
    _seed(
        sqlite_db,
        _systems(),
        [
            _incident(connector_source_system="cap_prod", interface_type="cap"),
            _incident(connector_source_system="cap_stage", interface_type="cap"),
            _incident(connector_source_system="SAP CAP", interface_type="cap"),
        ],
    )
    return sqlite_db


def test_lista_incorrelaciona_exato_fallback_e_ambiguo(seeded):
    rows = client.get("/admin/api/incidents", headers=_auth()).json()
    por_source = {r["connector_source_system"]: r for r in rows}

    assert por_source["cap_prod"]["system"]["match"] == MATCH_SYSTEM_KEY
    assert por_source["cap_prod"]["system"]["system_key"] == "cap_prod"
    assert por_source["cap_stage"]["system"]["match"] == MATCH_SYSTEM_KEY
    assert por_source["cap_stage"]["system"]["system_key"] == "cap_stage"
    # legado: 'SAP CAP' nao e system_key e 'cap' tem 2 candidatos -> ambiguo
    assert por_source["SAP CAP"]["system"]["match"] == MATCH_AMBIGUOUS
    assert por_source["SAP CAP"]["system"]["candidates"] == ["cap_prod", "cap_stage"]
    assert por_source["SAP CAP"]["system"]["system_key"] is None
    # campos do incidente preservados
    assert por_source["cap_prod"]["probable_root_cause"] == "material lock"
    assert por_source["cap_prod"]["verified_at"] is None


def test_lista_filtros_http(seeded):
    assert len(client.get("/admin/api/incidents?system_key=cap_stage", headers=_auth()).json()) == 1
    assert len(client.get("/admin/api/incidents?system_key=cap", headers=_auth()).json()) == 0
    assert len(client.get("/admin/api/incidents?interface_type=cap", headers=_auth()).json()) == 3
    assert len(client.get("/admin/api/incidents?interface_type=rfc", headers=_auth()).json()) == 0
    assert len(client.get("/admin/api/incidents?verified=false", headers=_auth()).json()) == 3
    assert len(client.get("/admin/api/incidents?verified=true", headers=_auth()).json()) == 0
    assert len(client.get("/admin/api/incidents?limit=2", headers=_auth()).json()) == 2
    assert client.get("/admin/api/incidents?limit=0", headers=_auth()).status_code == 422


def test_detalhe_de_incidente(seeded):
    rows = client.get("/admin/api/incidents", headers=_auth()).json()
    alvo = next(r for r in rows if r["connector_source_system"] == "cap_prod")
    detail = client.get(f"/admin/api/incidents/{alvo['id']}", headers=_auth())
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == alvo["id"]
    assert body["description"] == "IDoc 51 travado"
    assert body["evidence"] == [{"trust_level": "system_observed"}]
    assert body["system"]["system_key"] == "cap_prod"

    # a listagem nao carrega descricao/evidencia (payload enxuto)
    assert "description" not in alvo
    assert "evidence" not in alvo


def test_detalhe_inexistente_404(seeded):
    assert client.get(f"/admin/api/incidents/{uuid.uuid4()}", headers=_auth()).status_code == 404


def test_drill_down_nao_engole_outro_ambiente(seeded):
    """Regressao: com o indice montado so com `[system]`, o fallback por
    conector via 1 candidato e o drill-down de cap_prod devolveria tambem os
    incidentes de cap_stage e o legado."""
    catalogo = client.get("/admin/api/systems", headers=_auth()).json()
    ids = {s["system_key"]: s["id"] for s in catalogo}

    prod = client.get(f"/admin/api/systems/{ids['cap_prod']}/incidents", headers=_auth()).json()
    assert [r["connector_source_system"] for r in prod] == ["cap_prod"]

    stage = client.get(f"/admin/api/systems/{ids['cap_stage']}/incidents", headers=_auth()).json()
    assert [r["connector_source_system"] for r in stage] == ["cap_stage"]

    # OData: conector unico -> o legado resolve para o sistema
    legacy = client.get(
        f"/admin/api/systems/{ids['sap_odata_prod']}/incidents", headers=_auth()
    ).json()
    assert legacy == []

    assert (
        client.get(f"/admin/api/systems/{uuid.uuid4()}/incidents", headers=_auth()).status_code
        == 404
    )


def test_drill_down_do_conector_unico_resolve_legado(sqlite_db):
    """Com um unico sistema por conector, o fallback resolve inclusive os
    incidentes anteriores a DA-50 (connector_source_system = rotulo)."""
    _seed(
        sqlite_db,
        [
            {
                "system_key": "sap_odata_prod",
                "name": "S/4HANA OData",
                "vendor": "SAP",
                "connector_type": "odata",
            }
        ],
        [_incident(interface_type="odata", connector_source_system="OData")],
    )
    sistemas = client.get("/admin/api/systems", headers=_auth()).json()
    rows = client.get(f"/admin/api/systems/{sistemas[0]['id']}/incidents", headers=_auth()).json()
    assert len(rows) == 1
    assert rows[0]["system"]["match"] == MATCH_CONNECTOR_TYPE
    assert rows[0]["system"]["system_key"] == "sap_odata_prod"


def test_registry_status_conta_incidentes_e_pendentes(seeded):
    body = client.get("/admin/api/registry/status", headers=_auth()).json()
    assert body["incidents_count"] == 3
    assert body["unverified_count"] == 3
    assert body["systems_count"] == 3


def test_registry_status_sem_tabela_de_incidentes_nao_derruba(monkeypatch, sqlite_db):
    """Banco sem a tabela `incidents` (migration 001 nao aplicada) nao pode
    derrubar o status do registro."""

    async def _boom(*args, **kwargs):
        raise RuntimeError('relation "incidents" does not exist')

    monkeypatch.setattr(IncidentRepository, "count", _boom)
    body = client.get("/admin/api/registry/status", headers=_auth()).json()
    assert body["incidents_count"] is None
    assert body["unverified_count"] is None
    assert body["models_count"] is not None


def test_auth_das_rotas_de_incidentes():
    assert client.get("/admin/api/incidents").status_code == 401
    assert client.get("/admin/api/incidents", headers={"X-API-Admin-Key": "x"}).status_code == 401


def test_rotas_de_incidentes_sem_banco_503(_no_db):
    assert client.get("/admin/api/incidents", headers=_auth()).status_code == 503
    assert client.get(f"/admin/api/incidents/{uuid.uuid4()}", headers=_auth()).status_code == 503
    assert (
        client.get(f"/admin/api/systems/{uuid.uuid4()}/incidents", headers=_auth()).status_code
        == 503
    )


def test_ui_incidents_ok():
    r = client.get("/admin/incidents")
    assert r.status_code == 200
    assert "Incidentes diagnosticados" in r.text
    # a nav traz a tela nova e o link morto de /diagnose foi trocado por /docs
    assert 'href="/admin/incidents"' in r.text
    assert 'href="/diagnose"' not in r.text


# ---------------------------------------------------------------------------
# 4. record_verification (persistencia real, SQLite sincrono)
# ---------------------------------------------------------------------------


@pytest.fixture
def sync_sqlite(monkeypatch, tmp_path):
    """sessionmaker SQLite sincrono no lugar do engine psycopg2 (mesmo
    espelho de id UUID usado pelas rotas)."""
    engine = create_engine(f"sqlite:///{tmp_path / 'verify.db'}")
    IncidentSQLite.__table__.create(engine, checkfirst=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(incident_recorder, "_get_session_factory", lambda: factory)
    monkeypatch.setattr(incident_repo_module, "Incident", IncidentSQLite)
    monkeypatch.setattr(settings, "database_url", "postgresql://u:p@h/db")
    yield factory
    engine.dispose()


def test_record_verification_grava_veredito(sync_sqlite):
    incident_id = uuid.uuid4()
    with sync_sqlite() as session:
        session.add(IncidentSQLite(id=incident_id))
        session.commit()

    assert incident_recorder.record_verification(
        str(incident_id),
        diagnosis_correct=False,
        verified_by="ana",
        verified_root_cause="lock de material no ME21N",
    )

    with sync_sqlite() as session:
        row = session.get(IncidentSQLite, incident_id)
        assert row.diagnosis_correct is False
        assert row.verified_by == "ana"
        assert row.verified_root_cause == "lock de material no ME21N"
        assert row.verified_at is not None


def test_record_verification_correct_none_fica_null(sync_sqlite):
    """`correct` ausente = 'verificado sem veredito'. Coagir para True
    inflaria a taxa de acuracia dos dashboards com casos que o operador
    nao julizou."""
    incident_id = uuid.uuid4()
    with sync_sqlite() as session:
        session.add(IncidentSQLite(id=incident_id))
        session.commit()

    assert incident_recorder.record_verification(
        str(incident_id), diagnosis_correct=None, verified_by="ana"
    )
    with sync_sqlite() as session:
        row = session.get(IncidentSQLite, incident_id)
        assert row.diagnosis_correct is None
        assert row.verified_at is not None


def test_record_verification_inexistente_ou_invalido_retorna_false(sync_sqlite):
    assert incident_recorder.record_verification(str(uuid.uuid4()), diagnosis_correct=True) is False
    assert incident_recorder.record_verification("nao-e-uuid", diagnosis_correct=True) is False


def test_record_verification_sem_database_url_nao_conecta(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "")

    def _must_not_connect():
        raise AssertionError("nao deveria abrir conexao sem DATABASE_URL")

    monkeypatch.setattr(incident_recorder, "_get_session_factory", _must_not_connect)
    assert incident_recorder.record_verification(str(uuid.uuid4()), diagnosis_correct=True) is False


def test_record_verification_nao_levanta_quando_o_banco_quebra(monkeypatch):
    def _boom():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(settings, "database_url", "postgresql://u:p@h/db")
    monkeypatch.setattr(incident_recorder, "_get_session_factory", _boom)
    assert incident_recorder.record_verification(str(uuid.uuid4()), diagnosis_correct=True) is False


# ---------------------------------------------------------------------------
# 4b. Endpoint POST /incidents/{id}/verify — o efeito SQL na sumula
# ---------------------------------------------------------------------------


VERIFY_TOKEN = "verify-da50-token-01"


def _verify_settings(**kwargs) -> Settings:
    """Settings do endpoint de verify com a chave de API fixada (DA-18) e
    Langfuse desligado (langfuse_configured e property read-only, entao o
    objeto inteiro e substituido - mesmo padrao de tests/test_api.py)."""
    return Settings(api_key=VERIFY_TOKEN, **kwargs)


@pytest.fixture
def _api_key():
    return {"X-API-Key": VERIFY_TOKEN}


def test_verify_so_o_sql_gravou_200(monkeypatch, _api_key):
    """GraphRAG desligado, Langfuse desligado, mas o SQL persistiu: 200 com
    sql_updated=True. E o unico caminho em que a verificacao sobrevive sem
    Neo4j e sem Langfuse configurados."""
    import app.main as main_module

    monkeypatch.setattr(main_module, "settings", _verify_settings(graph_rag_enabled=False))
    monkeypatch.setattr(main_module, "record_verification", lambda *a, **k: True)

    body = client.post(
        f"/incidents/{uuid.uuid4()}/verify", headers=_api_key, json={"root_cause": "lock"}
    ).json()
    assert body["sql_updated"] is True
    assert body["graph_updated"] is False
    assert body["langfuse_scored"] is False


def test_verify_400_quando_nada_gravou(monkeypatch, _api_key):
    import app.main as main_module

    monkeypatch.setattr(main_module, "settings", _verify_settings(graph_rag_enabled=False))
    monkeypatch.setattr(main_module, "record_verification", lambda *a, **k: False)

    r = client.post(
        f"/incidents/{uuid.uuid4()}/verify", headers=_api_key, json={"root_cause": "lock"}
    )
    assert r.status_code == 400
    assert "incidents" in r.json()["detail"]


def test_verify_grava_no_sql_mesmo_com_404_do_grafo(monkeypatch, _api_key):
    """Efeitos independentes: o SQL roda ANTES do 404 do grafo, senao um
    incidente presente na tabela e ausente no Neo4j perderia o veredito -
    e o Neo4j e opcional (GraphRAG desligado por default)."""
    import app.main as main_module

    chamado: dict[str, Any] = {}

    def _fake_record(incident_id, **kwargs):
        chamado["id"] = incident_id
        chamado.update(kwargs)
        return True

    monkeypatch.setattr(main_module, "settings", _verify_settings(graph_rag_enabled=True))
    monkeypatch.setattr(main_module, "record_verification", _fake_record)
    monkeypatch.setattr(main_module, "verify_incident", lambda **kwargs: False)

    incident_id = str(uuid.uuid4())
    r = client.post(
        f"/incidents/{incident_id}/verify",
        headers=_api_key,
        json={"root_cause": "lock de material", "correct": True},
    )
    assert r.status_code == 404
    assert chamado["id"] == incident_id
    assert chamado["diagnosis_correct"] is True
    assert chamado["verified_root_cause"] == "lock de material"


def test_verify_nao_coage_correct_ausente_para_true(monkeypatch, _api_key):
    """Sem `correct`, a verificacao e gravada com diagnosis_correct=None."""
    import app.main as main_module

    capturado: dict[str, Any] = {}

    def _fake_record(incident_id, **kwargs):
        capturado.update(kwargs)
        return True

    monkeypatch.setattr(main_module, "settings", _verify_settings(graph_rag_enabled=False))
    monkeypatch.setattr(main_module, "record_verification", _fake_record)

    r = client.post(
        f"/incidents/{uuid.uuid4()}/verify",
        headers=_api_key,
        # verified_by e Literal["human"|"system"] (VerifyIncidentRequest)
        json={"root_cause": "lock", "verified_by": "system"},
    )
    assert r.status_code == 200
    assert capturado["diagnosis_correct"] is None
    assert capturado["verified_by"] == "system"


# ---------------------------------------------------------------------------
# 5. Propagacao request -> state -> incidents
# ---------------------------------------------------------------------------


def test_build_incident_row_preferencia_o_system_key_do_cliente(monkeypatch):
    from app.models import DiagnosisResponse, IncidentRequest

    class _Connector:
        source_system = "SAP CAP"
        is_mock = True

    response = DiagnosisResponse(
        probable_root_cause="lock",
        model_confidence=0.8,
        diagnosis_confidence=0.7,
        next_steps=[],
        report_markdown="",
    )
    request = IncidentRequest(description="IDoc 51", interface_type="cap")
    row = incident_recorder.build_incident_row(
        incident_id=str(uuid.uuid4()),
        request=request,
        response=response,
        final_state={"connector_data": _Connector(), "connector_source_system": "cap_prod"},
        latency_ms=10,
    )
    assert row["connector_source_system"] == "cap_prod"

    # sem o valor do cliente, continua o rotulo generico do conector
    row_fallback = incident_recorder.build_incident_row(
        incident_id=str(uuid.uuid4()),
        request=request,
        response=response,
        final_state={"connector_data": _Connector()},
        latency_ms=10,
    )
    assert row_fallback["connector_source_system"] == "SAP CAP"


def test_run_diagnosis_propaga_connector_source_system_ao_state(monkeypatch):
    import app.agent.graph as graph_module
    from app.models import IncidentRequest

    captured: dict[str, Any] = {}

    def _fake_invoke(initial_state):
        captured.update(initial_state)
        return {
            "diagnosis": {"probable_root_cause": "lock", "model_confidence": 0.8},
            "evidence": [],
        }

    monkeypatch.setattr(graph_module, "_invoke_graph_with_timeout", _fake_invoke)
    graph_module.run_diagnosis(
        IncidentRequest(
            description="IDoc 51", interface_type="cap", connector_source_system="cap_prod"
        )
    )
    assert captured["connector_source_system"] == "cap_prod"

    # ausente no request, a chave existe e vale None (nunca KeyError no state)
    captured.clear()
    graph_module.run_diagnosis(IncidentRequest(description="IDoc 51", interface_type="cap"))
    assert captured["connector_source_system"] is None


# ---------------------------------------------------------------------------
# 6. Dashboards: normalizacao + regressoes de SQL ja corrigidas
# ---------------------------------------------------------------------------


def _load_normalizer():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "validate_dashboards",
        pathlib.Path(__file__).resolve().parent.parent / "scripts" / "validate_dashboards.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalize_substitui_macros_do_grafana():
    vd = _load_normalizer()
    sql = (
        "SELECT $__timeGroupAlias(created_at, '1h'), COUNT(*) FROM incidents "
        "WHERE $__timeFilter(created_at) AND created_at >= $__timeFrom()::timestamptz"
    )
    out = vd.normalize_sql(sql, [])
    assert "date_trunc('hour', created_at)" in out
    assert "$__timeFilter" not in out
    assert "$__timeFrom" not in out
    assert vd._remaining_macros(out) == []


def test_normalize_trata_var_de_template_com_all():
    vd = _load_normalizer()
    sql = (
        "SELECT interface_type, COUNT(*) FROM incidents WHERE interface_type IS NOT NULL "
        "AND ($interface_type = 'All' OR interface_type = ANY(string_to_array('$interface_type', ',')))"
    )
    out = vd.normalize_sql(sql, ["interface_type"])
    assert vd._remaining_macros(out) == []
    # a clausula vira um "(TRUE OR ...)" -> sem filtro, como o Grafana faz
    # quando a variavel esta em "All"
    assert "(TRUE OR" in out


def test_time_group_literal_aceita_shorthand_do_grafana():
    vd = _load_normalizer()
    assert vd._time_group_literal("'1h'") == "hour"
    assert vd._time_group_literal("'30m'") == "30 minutes"
    assert vd._time_group_literal("'1d'") == "day"


def test_nenhum_dashboard_deixa_macro_por_substituir():
    """Se uma macro nova aparecer num dashboard, a validacao falha em vez
    de dar falso 'ok'."""
    vd = _load_normalizer()
    for path in sorted(_DASHBOARD_DIR.glob("*.json")):
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        variables = vd._vars_of(dashboard)
        for panel, target in vd._panels(dashboard):
            out = vd.normalize_sql(target["rawSql"], variables)
            assert vd._remaining_macros(out) == [], f"{path.name} / {panel.get('title')}: {out}"
            # coluna que exige cast: round(double, int) nao existe no Postgres
            assert "ROUND(AVG(" not in target["rawSql"].upper().replace("ROUND(AVG(", "")


def test_dashboard_de_sistemas_cobre_a_correlacao_da50():
    path = _DASHBOARD_DIR / "dashboard_systems.json"
    dashboard = json.loads(path.read_text(encoding="utf-8"))
    assert dashboard["uid"] == "iic-systems"
    sql = "\n".join(t["rawSql"] for _, t in _load_normalizer()._panels(dashboard))
    assert "integration_systems" in sql
    assert "connector_source_system" in sql
    assert "evidence_strength >= 0.7" in sql
    assert "IN ('high','critical')" not in sql


def test_evidence_strength_nunca_usado_como_texto_nos_scripts():
    """evidence_strength e FLOAT desde a migration 002. Qualquer predicado
    textual quebra a query inteira - no dashboard e no relatorio."""
    relatorio = (
        pathlib.Path(__file__).resolve().parent.parent / "scripts" / "generate_reports.py"
    ).read_text(encoding="utf-8")
    linhas = [ln for ln in relatorio.splitlines() if "evidence_strength" in ln and "IN (" in ln]
    assert linhas == []
    assert "evidence_strength >= 0.7" in relatorio
