"""DA-69 contra Postgres REAL: retomada e ausencia de PII nas tabelas do
checkpointer (inclusive valores `str` que o PostgresSaver grava inline no
jsonb, sem passar pelo serializer).

Marker `postgres_smoke`, NAO `integration` (mesmo padrao do
neo4j_smoke): o conftest pula `integration` quando Qdrant/Ollama estao
fora, e este teste so precisa de Postgres. Usa IIC_TEST_DATABASE_URL
(convencao do repo: o conftest zera DATABASE_URL para os testes) e pula
sozinho sem ela ou com o Postgres fora do ar:
`IIC_TEST_DATABASE_URL=postgresql://iic:<senha>@127.0.0.1:5432/iic uv run pytest -m postgres_smoke -v`. Usa um thread_id unico por
execucao e apaga as proprias linhas no fim.
"""

import os
import uuid

import pytest

from app.agent import graph
from app.agent.checkpointing import build_postgres_checkpointer, libpq_url

pytestmark = pytest.mark.postgres_smoke

EMAIL = "maria.souza@cliente.com.br"
CPF = "987.654.321-00"
DB_URL = os.environ.get("IIC_TEST_DATABASE_URL", "")


@pytest.fixture
def saver():
    if not DB_URL:
        pytest.skip("IIC_TEST_DATABASE_URL ausente")
    try:
        s = build_postgres_checkpointer(DB_URL)
    except Exception as exc:  # noqa: BLE001 - qualquer falha de conexao = skip
        pytest.skip(f"Postgres indisponivel: {exc}")
    yield s
    s.conn.close()


def _dump_thread(thread_id: str) -> str:
    from psycopg import connect

    with connect(libpq_url(DB_URL)) as conn:
        parts = []
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE thread_id = %s",
                (thread_id,),
            ).fetchall()
            parts.append(repr(rows))
        return "\n".join(parts)


def _cleanup(thread_id: str) -> None:
    from psycopg import connect

    with connect(libpq_url(DB_URL), autocommit=True) as conn:
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (thread_id,))


def test_postgres_resume_without_pii(saver, monkeypatch):
    monkeypatch.setattr(graph.settings, "graph_rag_enabled", False)
    monkeypatch.setattr(graph.settings, "ontology_enrichment_enabled", False)
    calls = {"retrieve": 0, "diagnose": 0}

    def retrieve(state):
        calls["retrieve"] += 1
        return {"retrieved_context": [{"source": f"nota de {EMAIL}"}]}

    def diagnose(state):
        calls["diagnose"] += 1
        if calls["diagnose"] == 1:
            raise RuntimeError("falha simulada")
        return {"diagnosis": {"probable_root_cause": "token"}}

    monkeypatch.setattr(graph, "supervisor_node", lambda s: {"agent_domain": "sap"})
    monkeypatch.setattr(graph, "connector_node", lambda s: {"connector_data": None})
    monkeypatch.setattr(graph, "retrieve_node", retrieve)
    monkeypatch.setattr(graph, "sap_diagnosis_node", diagnose)
    monkeypatch.setattr(graph, "report_node", lambda s: {"report_markdown": "ok"})

    thread_id = f"it-da69-{uuid.uuid4()}"
    cfg = {"configurable": {"thread_id": thread_id}}
    g = graph.build_graph(checkpointer=saver)
    try:
        with pytest.raises(RuntimeError):
            g.invoke({"description": f"erro para {EMAIL} CPF {CPF}", "incident_id": thread_id}, cfg)
        final = g.invoke(None, cfg)

        assert final["report_markdown"] == "ok"
        assert calls["retrieve"] == 1, "retrieve foi reexecutado na retomada"

        stored = _dump_thread(thread_id)
        assert stored.count(thread_id) > 0, "nada foi gravado para o thread"
        assert EMAIL not in stored
        assert CPF not in stored
    finally:
        _cleanup(thread_id)
