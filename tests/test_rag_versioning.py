"""DA-86 — testes de versionamento de documentos RAG.

Testa as rotas de administração de versões de documentos de conhecimento:
- /admin/knowledge/documents/{id}/versions
- /admin/knowledge/documents/{id}/rollback/{version}
- /admin/knowledge/documents/history/{id}

Sem DATABASE_URL as rotas devem devolver HTTP 503 "Persistencia nao configurada".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.admin.models import KnowledgeDocumentVersion
from app.config import settings
from app.main import app

ADMIN_KEY = "admin-test-key-01"

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fixed_admin_key(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", ADMIN_KEY)


def _auth(key: str | None = ADMIN_KEY) -> dict:
    if key is None:
        return {}
    return {"X-API-Admin-Key": key}


# --- sem banco = fail-closed 503 -------------------------------------------


def test_list_versions_sem_banco_503():
    """Rotas de versionamento devolvem 503 quando banco não configurado."""
    r = client.get("/admin/api/knowledge/documents/doc-123/versions", headers=_auth())
    assert r.status_code == 503
    assert "Persistencia" in r.json()["detail"]


def test_rollback_sem_banco_503():
    """Rollback devolve 503 quando banco não configurado."""
    r = client.post("/admin/api/knowledge/documents/doc-123/rollback/1", headers=_auth())
    assert r.status_code == 503


def test_history_sem_banco_503():
    """Histórico devolve 503 quando banco não configurado."""
    r = client.get("/admin/api/knowledge/documents/doc-123/history", headers=_auth())
    assert r.status_code == 503


def test_create_version_sem_banco_503():
    """Criação de versão devolve 503 quando banco não configurado."""
    r = client.post(
        "/admin/api/knowledge/documents/doc-123/versions",
        headers=_auth(),
        json={"content": "conteudo"},
    )
    assert r.status_code == 503


# --- tests com database URL -------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("db_url", ["sqlite+aiosqlite:///:memory:", None], ids=["sqlite", "no_db"])
def test_knowledge_versioning_lifecycle(db_url, monkeypatch, tmp_path):
    """Testa ciclo completo: criar, listar, rollback (DA-86)."""
    import asyncio

    from sqlalchemy.ext.asyncio import (
        AsyncEngine,
        AsyncSession,
        create_async_engine,
    )
    from sqlalchemy.orm import sessionmaker

    # Configurar banco se necessário
    if db_url:
        engine: AsyncEngine = create_async_engine(db_url)
        session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        monkeypatch.setattr("app.db.get_db_session", lambda: session_factory())

        # Criar tabelas
        async def setup_db():
            async with engine.begin() as conn:
                await conn.run_sync(KnowledgeDocumentVersion.metadata.create_all)

        asyncio.run(setup_db())
    else:
        # Sem banco, as rotas devem falhar
        r = client.get("/admin/api/knowledge/documents/doc-123/versions", headers=_auth())
        assert r.status_code == 503
        return

    document_id = "test-doc-001"

    # Criar versão 1
    r = client.post(
        "/admin/api/knowledge/documents/doc-123/versions",
        headers=_auth(),
        json={"content": "# Documento v1\nConteúdo da versão 1"},
    )
    assert r.status_code == 201
    v1 = r.json()
    assert v1["version"] == 1
    assert v1["is_current"] is False

    # Criar versão 2
    r = client.post(
        "/admin/api/knowledge/documents/doc-123/versions",
        headers=_auth(),
        json={"content": "# Documento v2\nConteúdo da versão 2", "is_current": True},
    )
    assert r.status_code == 201
    v2 = r.json()
    assert v2["version"] == 2
    assert v2["is_current"] is True

    # Listar versões
    r = client.get("/admin/api/knowledge/documents/doc-123/versions", headers=_auth())
    assert r.status_code == 200
    data = r.json()
    assert data["document_id"] == document_id
    assert len(data["versions"]) == 2
    assert data["current_version"] == 2

    # Ver histórico
    r = client.get("/admin/api/knowledge/documents/doc-123/history", headers=_auth())
    assert r.status_code == 200
    history = r.json()
    assert len(history["versions"]) == 2

    # Fazer rollback para v1
    r = client.post(
        f"/admin/api/knowledge/documents/doc-123/rollback/{v1['version']}",
        headers=_auth(),
    )
    assert r.status_code == 200
    assert r.json()["current_version"] == 1

    # Verificar que v1 agora é current
    r = client.get("/admin/api/knowledge/documents/doc-123/versions", headers=_auth())
    assert r.status_code == 200
    versions = r.json()["versions"]
    v1_restored = next(v for v in versions if v["version"] == 1)
    v2_restored = next(v for v in versions if v["version"] == 2)
    assert v1_restored["is_current"] is True
    assert v2_restored["is_current"] is False

    # Criar versão 3
    r = client.post(
        "/admin/api/knowledge/documents/doc-123/versions",
        headers=_auth(),
        json={"content": "# Documento v3\nConteúdo da versão 3"},
    )
    assert r.status_code == 201
    v3 = r.json()
    assert v3["version"] == 3

    # Listar todas as 3 versões
    r = client.get("/admin/api/knowledge/documents/doc-123/versions", headers=_auth())
    assert r.status_code == 200
    data = r.json()
    assert len(data["versions"]) == 3
    assert data["current_version"] == 1  # v1 ainda é current (não mudamos)

    # Testar erro: versão já existe
    r = client.post(
        "/admin/api/knowledge/documents/doc-123/versions",
        headers=_auth(),
        json={"content": "# Duplicado"},
    )
    assert r.status_code == 409
    assert "versão 4" in r.json()["detail"]

    # Testar erro: rollback para versão inexistente
    r = client.post(
        "/admin/api/knowledge/documents/doc-123/rollback/999",
        headers=_auth(),
    )
    assert r.status_code == 404
    assert "versão 999" in r.json()["detail"]

    # Testar erro: documento inexistente
    r = client.get("/admin/api/knowledge/documents/nonexistent/versions", headers=_auth())
    assert r.status_code == 404
    assert "não encontrado" in r.json()["detail"]


@pytest.mark.integration
def test_knowledge_versioning_integrity():
    """Testa integridade dos dados (DA-86)."""
    import asyncio

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    # Usar banco em memória
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def setup_and_test():
        async with engine.begin() as conn:
            await conn.run_sync(KnowledgeDocumentVersion.metadata.create_all)

        async with session_factory() as session:
            # Criar documento com múltiplas versões
            doc_id = "integrity-test-001"
            versions_data = [
                {"document_id": doc_id, "version": 1, "content": "v1", "is_current": False},
                {"document_id": doc_id, "version": 2, "content": "v2", "is_current": True},
                {"document_id": doc_id, "version": 3, "content": "v3", "is_current": False},
            ]

            for vdata in versions_data:
                version = KnowledgeDocumentVersion(**vdata)
                session.add(version)

            await session.commit()

            # Verificar que apenas uma é current
            result = await session.execute(
                select(KnowledgeDocumentVersion).where(
                    KnowledgeDocumentVersion.document_id == doc_id,
                    KnowledgeDocumentVersion.is_current.is_(True),
                )
            )
            current_versions = result.scalars().all()
            assert len(current_versions) == 1, "Apenas uma versão deve ser current"

            # Verificar que conteúdos são preservados
            for vdata in versions_data:
                result = await session.execute(
                    select(KnowledgeDocumentVersion).where(
                        KnowledgeDocumentVersion.document_id == doc_id,
                        KnowledgeDocumentVersion.version == vdata["version"],
                    )
                )
                v = result.scalar_one()
                assert v.content == vdata["content"]

    asyncio.run(setup_and_test())
