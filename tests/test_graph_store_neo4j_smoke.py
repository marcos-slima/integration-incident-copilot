"""Smoke test do GraphRAG contra um Neo4j REAL - avaliacao externa
(medio prazo, item 7): "smoke test com Neo4j no CI (service
container)".

Os testes existentes de app/rag/graph_store.py (tests/test_graph_store.py)
usam um `FakeSession`/`FakeDriver` em memoria - provam a LOGICA de
upsert/verify/query (parametros passados, filtragem de
grounded/verified, formatacao do prompt), mas nunca foram executados
contra um Neo4j de verdade. A revisao externa apontou isso
explicitamente como risco: "GraphRAG: codigo existe e e testado com
fake driver; nao validado contra Neo4j real. Risco de
schema/constraints em runtime."

Este arquivo fecha essa lacuna com um teste minimo, mas end-to-end,
contra um Neo4j real: cria as constraints (ensure_constraints), grava
um incidente (upsert_incident_graph), confirma que ele aparece no
contexto relacional (graph_context) e que verify_incident() marca
`verified=true` e passa a aparecer mesmo com evidence_strength baixa
(o comportamento de "verificacao humana supera o proxy automatico" -
ver DA-28 - so tem valor provado se realmente rodar contra o Cypher
de verdade, nao contra um fake que nunca rejeitaria uma query mal
formada).

So roda de verdade no job dedicado "neo4j-smoke" do CI
(.github/workflows/tests.yml), que sobe um Neo4j real como service
container e seleciona so estes testes com "pytest -m neo4j_smoke".

Marcado APENAS com @pytest.mark.neo4j_smoke (nao com
@pytest.mark.integration) de proposito: o hook
tests/conftest.py::pytest_collection_modifyitems pula TODO teste
marcado "integration" sempre que Qdrant/Ollama nao estiverem
acessiveis em 127.0.0.1 - o que tambem seria verdade dentro do job
"neo4j-smoke" (ele so sobe Neo4j, nao Qdrant/Ollama), fazendo esses
testes serem pulados exatamente no job criado para roda-los de
verdade. Em vez disso, a propria fixture `real_graph_session` abaixo
faz o pytest.skip() explicito quando NEO4J_URI/NEO4J_PASSWORD nao
estao configuradas - o que cobre o caso "sem Neo4j disponivel" (rodar
localmente, ou no job padrao "test" do CI, com
"pytest -m 'not integration'") sem depender do marker "integration".
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = [pytest.mark.neo4j_smoke]


@pytest.fixture
def real_graph_session(monkeypatch):
    """Aponta app.rag.graph_store para um Neo4j real via variaveis de
    ambiente (NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD - as mesmas que o
    job "neo4j-smoke" do CI exporta) e devolve uma sessao real aberta,
    fechada ao fim do teste. Pulado (nao falhado) se essas variaveis
    nao estiverem configuradas - permite rodar a suite completa
    ('-m neo4j_smoke') numa maquina sem Neo4j sem quebrar, mesmo
    espirito do marker "integration" para Qdrant/Ollama."""
    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD")
    if not uri or not password:
        pytest.skip(
            "NEO4J_URI/NEO4J_PASSWORD nao configurados - este smoke test requer um "
            "Neo4j real (ver job 'neo4j-smoke' do CI, ou exporte as variaveis "
            "localmente com um Neo4j em docker compose --profile graphrag up -d neo4j)."
        )

    from app.config import Settings

    real_settings = Settings(
        graph_rag_enabled=True, neo4j_uri=uri, neo4j_user=user, neo4j_password=password
    )
    monkeypatch.setattr("app.rag.graph_store.settings", real_settings)

    import app.rag.graph_store as graph_store_module

    graph_store_module._get_driver.cache_clear()
    driver = graph_store_module._get_driver()
    session = driver.session()
    try:
        yield session
    finally:
        session.close()
        graph_store_module._get_driver.cache_clear()


def test_ensure_constraints_is_idempotent_against_real_neo4j(real_graph_session):
    from app.rag.graph_store import ensure_constraints

    # "IF NOT EXISTS" nas constraints (ver app/rag/graph_store.py,
    # _CONSTRAINTS) precisa tolerar ser chamado mais de uma vez sem
    # erro - e exatamente o que acontece a cada "docker compose up"
    # com o profile "graphrag" ja tendo rodado antes.
    ensure_constraints(session=real_graph_session)
    ensure_constraints(session=real_graph_session)


def test_upsert_and_graph_context_roundtrip_against_real_neo4j(real_graph_session):
    from app.rag.graph_store import ensure_constraints, graph_context, upsert_incident_graph

    ensure_constraints(session=real_graph_session)

    incident_id = f"smoke-{uuid.uuid4()}"
    identifier = f"CPI-SMOKE-{uuid.uuid4()}"
    try:
        upsert_incident_graph(
            incident_id=incident_id,
            description="Smoke test - iFlow falhando com 401",
            interface_type="odata",
            identifier=identifier,
            source_system="CPI",
            root_cause="Certificado expirado (smoke test)",
            confidence=0.9,
            matched_document="cpi_http_401.md",
            evidence_strength=0.8,  # >= GROUNDED_EVIDENCE_THRESHOLD
            session=real_graph_session,
        )

        related = graph_context(
            interface_type="odata", identifier=identifier, session=real_graph_session
        )

        assert len(related) == 1
        assert related[0].root_cause == "Certificado expirado (smoke test)"
        assert related[0].source_system == "CPI"
        assert related[0].matched_document == "cpi_http_401.md"
        assert related[0].is_grounded is True
        assert related[0].verified is False
    finally:
        real_graph_session.run("MATCH (i:Incident {id: $id}) DETACH DELETE i", id=incident_id)


def test_verify_incident_marks_verified_and_overrides_ungrounded_filter(real_graph_session):
    from app.rag.graph_store import (
        ensure_constraints,
        graph_context,
        upsert_incident_graph,
        verify_incident,
    )

    ensure_constraints(session=real_graph_session)

    incident_id = f"smoke-{uuid.uuid4()}"
    identifier = f"CPI-SMOKE-{uuid.uuid4()}"
    try:
        upsert_incident_graph(
            incident_id=incident_id,
            description="Smoke test - IDoc travado",
            interface_type="odata",
            identifier=identifier,
            source_system="CPI",
            root_cause="Hipotese original do LLM (baixa confianca)",
            confidence=0.4,
            matched_document=None,
            evidence_strength=0.1,  # abaixo do threshold - NAO grounded
            session=real_graph_session,
        )

        # Antes da verificacao: hipotese nao-grounded fica de fora do
        # contexto por padrao (include_ungrounded=False).
        before = graph_context(
            interface_type="odata", identifier=identifier, session=real_graph_session
        )
        assert before == []

        verified = verify_incident(
            incident_id=incident_id,
            verified_root_cause="Causa raiz CONFIRMADA (smoke test)",
            verified_by="human",
            session=real_graph_session,
        )
        assert verified is True

        # Depois: verified=true supera o filtro de grounded (DA-28).
        after = graph_context(
            interface_type="odata", identifier=identifier, session=real_graph_session
        )
        assert len(after) == 1
        assert after[0].verified is True
        assert after[0].verified_root_cause == "Causa raiz CONFIRMADA (smoke test)"
    finally:
        real_graph_session.run("MATCH (i:Incident {id: $id}) DETACH DELETE i", id=incident_id)


def test_verify_incident_returns_false_for_unknown_incident_id(real_graph_session):
    from app.rag.graph_store import verify_incident

    verified = verify_incident(
        incident_id=f"nao-existe-{uuid.uuid4()}",
        verified_root_cause="x",
        session=real_graph_session,
    )
    assert verified is False
