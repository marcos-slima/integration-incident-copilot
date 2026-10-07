"""DA-52: prova de que o caminho COMPLETO funciona, nao so as unidades.

Estes testes atravessam conector -> probe HTTP -> parser -> baseline no
PostgreSQL -> diff -> evento CloudEvents. Cada etapa tem teste proprio
(test_contracts_odata, test_contracts_diff, test_contracts_observe); o que
falha aqui e a EMPLACAMENTO, que e' onde bug de cola costuma morar --
campo que o um passa e o outro nao le, atributo nomeado diferente, estado
que o relay perde.

O banco e' real (PostgreSQL descartavel, como no job de migrations da
DA-51). Um fake de session nao pegaria divergencia de tipo de coluna,
indice ou mapping do ORM -- que e' parte do que esta sendo verificado.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("IIC_TEST_DATABASE_URL"),
    reason="IIC_TEST_DATABASE_URL ausente (banco descartavel para a DA-52)",
)


def _database_url() -> str:
    """Le a URL de dentro da funcao, e nao no import.

    `pytest.mark.skipif` so impede a EXECUCAO dos testes: o modulo ainda e'
    importado na coleta. Ler `os.environ[...]` no topo do arquivo estouraria
    KeyError na suite normal -- que e' justamente onde esses testes devem
    ser silenciosos.
    """
    return os.environ["IIC_TEST_DATABASE_URL"]


EDMX_V1 = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">
  <edmx:DataServices>
    <Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="Self">
      <EntityType Name="I_Message">
        <Property Name="MessageId" Type="Edm.String" MaxLength="10">
          <Annotation Term="Org.OData.Core.V1.Key"/>
        </Property>
        <Property Name="StatusText" Type="Edm.String" Nullable="true" MaxLength="80"/>
      </EntityType>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""

#: O conector le `StatusText` (odata_connector.py:177). Remover este campo
#: e' exatamente o modo de falha que a DA-52 existe para pegar.
EDMX_V2_SEM_STATUS_TEXT = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">
  <edmx:DataServices>
    <Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="Self">
      <EntityType Name="I_Message">
        <Property Name="MessageId" Type="Edm.String" MaxLength="10">
          <Annotation Term="Org.OData.Core.V1.Key"/>
        </Property>
      </EntityType>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""

#: Mesmo contrato, namespace novo e anotacao de version nova: e' o que o
#: SAP Gateway devolve a cada publicacao. Nao pode virar drift.
EDMX_V1_REPUBLICADO = EDMX_V1.replace(
    "</edmx:DataServices>",
    '<Annotation Term="OData.Community.V1.VocabularyMetadata">'
    '<Annotation Term="OData.Community.V1.VocabularyMetadata#Version" String="4.0.1"/>'
    "</Annotation></edmx:DataServices>",
).replace('Namespace="Self"', 'Namespace="Self.a1b2c3"')


def _make_handler(state: dict[str, Any]) -> type[BaseHTTPRequestHandler]:
    """Cria a classe do handler fechando sobre o `state` do servidor.

    Deliberadamente NAO um ClassVar: um dicionario de estado em atributo de
    classe e' compartilhado por todos os testes do processo, e o teste que
    roda antes deixa o stub "sujo" para o seguinte -- o detector passa a
    reportar clean/first_observation errado e a falha aparece no teste
    errado. Fechar sobre o dicionario torna o estadoPor-servidor.
    """

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # assinatura do BaseHTTPRequestHandler
            body = state["metadata"].encode("utf-8")
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            # OAuth token_url: responde o token para o conector chegar ao
            # `$metadata` sem configuracao real de credencial.
            body = json.dumps({"access_token": "token-de-teste", "token_type": "Bearer"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            return

    return _Handler


@pytest.fixture
def sap_stub():
    # Estado novo por teste: comecar sempre em EDMX_V1.
    state: dict[str, Any] = {"metadata": EDMX_V1, "status": 200}
    server = HTTPServer(("127.0.0.1", 0), _make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield type("Stub", (), {"base": base, "state": state})()
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch):
    """Aplica a migration 005 num banco limpo e aponta o app para ele."""
    from sqlalchemy import text

    from app.contracts import observe as observe_module
    from app.contracts.baseline import SystemContract
    from app.db import get_sync_session_factory, reset_sync_session_factory

    # Aponta o app para o banco descartavel ANTES de pedir a factory: ela
    # e' cacheada por URL, e pedir antes devolveria um pool para outro banco.
    database_url = _database_url()
    monkeypatch.setattr(observe_module.settings, "database_url", database_url)
    reset_sync_session_factory()

    subprocess.run(
        [".venv/bin/alembic", "upgrade", "head"],
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        check=True,
    )
    factory = get_sync_session_factory()
    assert factory is not None, "get_sync_session_factory deveria existir com DATABASE_URL"
    with factory() as session:
        session.execute(text("TRUNCATE system_contracts"))
        session.commit()
    assert SystemContract.__tablename__ == "system_contracts"
    yield factory
    with factory() as session:
        session.execute(text("TRUNCATE system_contracts"))
        session.commit()


@pytest.fixture
def odata_config(monkeypatch: pytest.MonkeyPatch, sap_stub):
    monkeypatch.setattr(
        "app.connectors.odata_connector.settings.odata_service_url", sap_stub.base, raising=False
    )
    monkeypatch.setattr(
        "app.connectors.odata_connector.settings.odata_oauth_token_url",
        f"{sap_stub.base}/token",
        raising=False,
    )
    monkeypatch.setattr(
        "app.connectors.odata_connector.settings.odata_client_id", "cid", raising=False
    )
    monkeypatch.setattr(
        "app.connectors.odata_connector.settings.odata_client_secret", "secret", raising=False
    )
    return sap_stub


def run(system_key: str, *, emit: bool = False):
    from app.connectors import get_connector
    from app.contracts.observe import check_connector

    return check_connector(
        get_connector("odata"), system_key=system_key, connector_type="odata", emit=emit
    )


class TestCicloCompleto:
    def test_primeira_observacao_cria_baseline_no_banco(self, db, odata_config):
        report = run("cap_prod_a")
        assert report.status.value == "first_observation"
        with db() as session:
            rows = list(session.query(_all_contracts()).all())
        assert len(rows) == 1
        assert rows[0].fingerprint == report.fingerprint_after
        assert rows[0].contract_kind == "odata_v4"

    def test_republicacao_sem_mudanca_nao_e_drift(self, db, odata_config):
        run("cap_prod_b")
        # Namespace novo + Annotation de Version: o SAP republicando.
        odata_config.state["metadata"] = EDMX_V1_REPUBLICADO
        report = run("cap_prod_b")
        assert report.status.value == "clean"
        assert report.changes == ()

    def test_campo_removido_no_segundo_poll_e_breaking(self, db, odata_config):
        run("cap_prod_c")
        odata_config.state["metadata"] = EDMX_V2_SEM_STATUS_TEXT
        report = run("cap_prod_c")
        assert report.status.value == "drift"
        assert report.severity == "breaking"
        assert any(c.property == "StatusText" for c in report.changes)

    def test_historico_guarda_as_duas_observacoes(self, db, odata_config):
        run("cap_prod_d")
        odata_config.state["metadata"] = EDMX_V2_SEM_STATUS_TEXT
        run("cap_prod_d")
        with db() as session:
            rows = list(session.query(_all_contracts()).filter_by(system_key="cap_prod_d").all())
        assert len(rows) == 2
        assert rows[0].observation_status == "first_observation"
        assert rows[1].observation_status == "drift"
        assert rows[0].fingerprint != rows[1].fingerprint

    def test_sap_fora_do_ar_e_unverified_e_nao_apaga_o_baseline(self, db, odata_config):
        run("cap_prod_e")
        odata_config.state["status"] = 500
        report = run("cap_prod_e")
        assert report.status.value == "unverified"
        with db() as session:
            rows = list(session.query(_all_contracts()).filter_by(system_key="cap_prod_e").all())
        assert len(rows) == 1, "unverified nao pode gravar nem apagar linha"

    def test_sap_de_volta_e_compara_ainda_com_o_baseline_antigo(self, db, odata_config):
        run("cap_prod_f")
        odata_config.state["status"] = 503
        run("cap_prod_f")
        odata_config.state["status"] = 200
        odata_config.state["metadata"] = EDMX_V2_SEM_STATUS_TEXT
        report = run("cap_prod_f")
        assert report.severity == "breaking", "a queda de leitura nao pode zerar o baseline"

    def test_drift_breaking_vira_incidente_com_system_key(self, db, odata_config, monkeypatch):
        from app.models import IncidentEventData

        capturado: dict[str, Any] = {}

        def fake_handle(envelope: Any) -> Any:
            capturado["envelope"] = envelope
            return None

        from app.events import consumer

        monkeypatch.setattr(consumer, "handle_incident_event", fake_handle)
        run("cap_prod_g")
        odata_config.state["metadata"] = EDMX_V2_SEM_STATUS_TEXT
        report = run("cap_prod_g", emit=True)
        assert report.is_breaking
        envelope = capturado["envelope"]
        assert isinstance(envelope.data, IncidentEventData)
        assert envelope.data.connector_source_system == "cap_prod_g"
        assert "StatusText" in envelope.data.description
        assert envelope.source == "schema-drift-detector"

    def test_dois_sistemas_nao_compartilham_baseline(self, db, odata_config):
        run("cap_prod_h")
        odata_config.state["metadata"] = EDMX_V2_SEM_STATUS_TEXT
        assert run("cap_prod_i").status.value == "first_observation", (
            "cada system_key tem o proprio baseline"
        )

    def test_cli_reporta_o_drift_com_codigo_de_saida_1(self, db, odata_config, tmp_path):
        env = {
            **os.environ,
            "DATABASE_URL": _database_url(),
            "ODATA_SERVICE_URL": odata_config.base,
            "ODATA_OAUTH_TOKEN_URL": f"{odata_config.base}/token",
            "ODATA_CLIENT_ID": "cid",
            "ODATA_CLIENT_SECRET": "secret",
        }
        key = f"cap_cli_{uuid.uuid4().hex[:8]}"
        primeiro = subprocess.run(
            [
                sys.executable,
                "scripts/check_contract_drift.py",
                "--system-key",
                key,
                "--connector-type",
                "odata",
                "--json",
                "--no-emit",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert primeiro.returncode == 0, primeiro.stderr
        assert json.loads(primeiro.stdout)["status"] == "first_observation"

        odata_config.state["metadata"] = EDMX_V2_SEM_STATUS_TEXT
        segundo = subprocess.run(
            [
                sys.executable,
                "scripts/check_contract_drift.py",
                "--system-key",
                key,
                "--connector-type",
                "odata",
                "--json",
                "--no-emit",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert segundo.returncode == 1, "breaking tem de sair != 0 para o CI"
        payload = json.loads(segundo.stdout)
        assert payload["severity"] == "breaking"
        assert payload["counts"]["breaking"] >= 1


def _all_contracts():
    from app.contracts.baseline import SystemContract

    return SystemContract
