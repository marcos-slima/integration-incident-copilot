"""DATA-01 e DB-01 (validacao 2026-10-07)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import app.contracts.observe as O
from app.contracts.diff import ObservationStatus, diff_contracts
from app.contracts.odata import parse_odata_metadata

_X = (
    '<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">'
    "<edmx:DataServices>"
    '<Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="S">'
    '<EntityType Name="E"><Key><PropertyRef Name="id"/></Key>'
    '<Property Name="id" Type="Edm.String" Nullable="false"/>'
    '<Property Name="obs" Type="Edm.String"/></EntityType>'
    "</Schema></edmx:DataServices></edmx:Edmx>"
)
_P = '<Property Name="obs" Type="Edm.String"/>'


def _c(xml: str = _X):
    return parse_odata_metadata(xml)


def test_maxlength_novo_e_breaking() -> None:
    r = diff_contracts(_c(), _c(_X.replace(_P, _P.replace("/>", ' MaxLength="10"/>'))))
    assert r.severity == "breaking"
    assert [c.kind for c in r.changes] == ["max_length_added"]


def test_maxlength_maior_continua_additive() -> None:
    antes = _c(_X.replace(_P, _P.replace("/>", ' MaxLength="10"/>')))
    depois = _c(_X.replace(_P, _P.replace("/>", ' MaxLength="20"/>')))
    assert diff_contracts(antes, depois).severity == "additive"


@pytest.fixture
def chamadas(monkeypatch):
    log: list[str] = []
    monkeypatch.setattr(
        O, "_persist", lambda report, contract, connector_type: log.append("persist")
    )
    return log


def test_emissao_falha_nao_grava_baseline(monkeypatch, chamadas) -> None:
    """Antes: emit falhava e o baseline novo era gravado - drift perdido."""
    monkeypatch.setattr(O, "_load_baseline", lambda key: _c())
    monkeypatch.setattr(O, "emit_incident", lambda report, connector_type: False)
    novo = _c(_X.replace(_P, _P.replace("/>", ' Nullable="false"/>')))
    r = O.observe(system_key="S1", contract=novo, connector_type="odata")
    assert r.is_breaking and chamadas == []


def test_emissao_ok_grava_baseline(monkeypatch, chamadas) -> None:
    monkeypatch.setattr(O, "_load_baseline", lambda key: _c())
    monkeypatch.setattr(O, "emit_incident", lambda report, connector_type: True)
    novo = _c(_X.replace(_P, _P.replace("/>", ' Nullable="false"/>')))
    O.observe(system_key="S1", contract=novo, connector_type="odata")
    assert chamadas == ["persist"]


def test_sem_drift_grava_mesmo_sem_emitir(monkeypatch, chamadas) -> None:
    monkeypatch.setattr(O, "_load_baseline", lambda key: _c())
    monkeypatch.setattr(O, "emit_incident", lambda report, connector_type: False)
    r = O.observe(system_key="S1", contract=_c(), connector_type="odata")
    assert r.status == ObservationStatus.CLEAN and chamadas == ["persist"]


def test_erro_ao_ler_baseline_vira_unverified(monkeypatch, chamadas) -> None:
    """Antes: erro de banco virava first_observation e o contrato atual era
    gravado como baseline, absorvendo o drift."""

    def _quebra(key):
        raise O.BaselineReadError("OperationalError")

    monkeypatch.setattr(O, "_load_baseline", _quebra)
    r = O.observe(system_key="S1", contract=_c(), connector_type="odata")
    assert r.status == ObservationStatus.UNVERIFIED and chamadas == []


def test_metadata_do_alembic_cobre_todas_as_tabelas_das_migrations() -> None:
    """DB-01: tabela criada por migration e ausente do metadata do env.py
    vira DROP TABLE no `alembic revision --autogenerate`."""
    raiz = Path(__file__).resolve().parents[1]
    criadas: set[str] = set()
    for mig in (raiz / "alembic" / "versions").glob("*.py"):
        criadas |= set(re.findall(r'op\.create_table\(\s*"(\w+)"', mig.read_text()))
    env = (raiz / "alembic" / "env.py").read_text()
    imports = re.findall(r"^from (app\.[\w.]+) import", env, flags=re.MULTILINE)
    import importlib

    for mod in imports:
        importlib.import_module(mod)
    from app.db import Base

    assert criadas, "nenhum create_table encontrado"
    assert criadas <= set(Base.metadata.tables), sorted(criadas - set(Base.metadata.tables))


def test_no_emit_grava_baseline_e_nao_emite(monkeypatch, chamadas) -> None:
    emitidos = []
    monkeypatch.setattr(O, "_load_baseline", lambda key: _c())
    monkeypatch.setattr(O, "emit_incident", lambda report, connector_type: emitidos.append(1))
    novo = _c(_X.replace(_P, _P.replace("/>", ' Nullable="false"/>')))
    r = O.observe(system_key="S1", contract=novo, connector_type="odata", emit=False)
    assert emitidos == [] and chamadas == ["persist"] and r.incident_emitted is False


def test_cli_nao_emite_duas_vezes(monkeypatch) -> None:
    """Antes observe() emitia por dentro e o CLI emitia de novo."""
    import importlib

    cli = importlib.import_module("scripts.check_contract_drift")
    assert not hasattr(cli, "emit_incident")
