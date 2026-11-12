"""Gap A5 (DA-79) — multi-tenancy nao fail-closed.

O estudo IIC->SAP AMS/DF-e aponta que `tenant_id` existe na requisicao,
nos filtros de repositorio e nas credenciais, mas nao chega ao grafo:
`app/agent/graph.py` nao propagava `request.tenant_id` para o
`CopilotState` e `build_incident_row()` nao gravava a coluna. Resultado:
todo incidente diagnosticado ficava com `tenant_id=NULL`, independente
do tenant informado - o isolamento por tenant nunca era exercitado no
caminho real.

Estes testes documentam o comportamento esperado:

1. `tenant_id` informado na requisicao chega ao estado inicial do grafo.
2. `tenant_id` do estado e persistido no registro do incidente.
3. Requisicao sem tenant continua funcionando (`tenant_id=None`),
   sem inventar escopo.
4. As consultas de leitura por tenant sao fail-closed (o filtro por
   `tenant_id` esta no SQL, nao em Python depois da leitura).

O que NAO esta coberto aqui (limitacao registrada de proposito): a
identidade autenticada ainda nao carrega tenant, entao `tenant_id` e um
campo informado pelo chamador - nao uma fronteira de autorizacao. Ver
docs/DECISOES_DE_ARQUITETURA.md §73 (DA-79, candidata).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent import graph
from app.models import IncidentRequest
from app.services import incident_recorder


def _captured_initial_state(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Executa run_diagnosis com o grafo stubado e devolve o estado inicial."""
    seen: dict[str, Any] = {}

    def fake_invoke(initial_state, timeout_seconds=None, thread_id=None):
        seen.update(initial_state)
        return {
            **initial_state,
            "diagnosis": {"probable_root_cause": "causa", "confidence": 0.5},
            "report_markdown": "",
        }

    monkeypatch.setattr(graph, "_invoke_graph_with_timeout", fake_invoke)
    monkeypatch.setattr(graph, "record_incident", lambda *a, **k: None)
    return seen


def _request(**overrides: Any) -> IncidentRequest:
    return IncidentRequest(description="OData 401 apos refresh de token", **overrides)


class TestTenantReachesGraph:
    def test_tenant_id_chega_ao_estado_inicial(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = _captured_initial_state(monkeypatch)

        graph.run_diagnosis(_request(tenant_id="acme"))

        assert seen["tenant_id"] == "acme"

    def test_sem_tenant_nao_inventa_escopo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = _captured_initial_state(monkeypatch)

        graph.run_diagnosis(_request())

        assert seen["tenant_id"] is None

    def test_state_declara_tenant_id(self) -> None:
        """Chave fora do CopilotState e descartada pelo StateGraph."""
        from app.agent.state import CopilotState

        assert "tenant_id" in CopilotState.__annotations__


class TestTenantIsPersisted:
    def _row(self, **final_state_overrides: Any) -> dict[str, Any]:
        request = _request(tenant_id="acme")
        response = graph.DiagnosisResponse(
            probable_root_cause="causa",
            model_confidence=0.5,
            diagnosis_confidence=0.25,
            next_steps=[],
            report_markdown="",
        )
        final_state = {
            "description": request.description,
            "tenant_id": request.tenant_id,
            **final_state_overrides,
        }
        return incident_recorder.build_incident_row(
            incident_id="00000000-0000-4000-8000-000000000001",
            request=request,
            response=response,
            final_state=final_state,
            latency_ms=10,
        )

    def test_build_incident_row_grava_tenant_do_estado(self) -> None:
        row = self._row()
        assert row["tenant_id"] == "acme"

    def test_estado_prevalece_sobre_requisicao(self) -> None:
        """O estado e' a fonte do que o pipeline realmente usou."""
        row = self._row(tenant_id="estado")
        assert row["tenant_id"] == "estado"

    def test_fallback_para_requisicao_quando_estado_nao_tem(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        request = _request(tenant_id="acme")
        response = graph.DiagnosisResponse(
            probable_root_cause="causa",
            model_confidence=0.5,
            diagnosis_confidence=0.25,
            next_steps=[],
            report_markdown="",
        )
        row = incident_recorder.build_incident_row(
            incident_id="00000000-0000-4000-8000-000000000002",
            request=request,
            response=response,
            final_state={"description": request.description},
            latency_ms=10,
        )
        assert row["tenant_id"] == "acme"

    def test_sem_tenant_grava_null(self) -> None:
        request = _request()
        response = graph.DiagnosisResponse(
            probable_root_cause="causa",
            model_confidence=0.5,
            diagnosis_confidence=0.25,
            next_steps=[],
            report_markdown="",
        )
        row = incident_recorder.build_incident_row(
            incident_id="00000000-0000-4000-8000-000000000003",
            request=request,
            response=response,
            final_state={"description": request.description, "tenant_id": None},
            latency_ms=10,
        )
        assert row["tenant_id"] is None


class _CapturingSession:
    """Sessao falsa que so guarda o statement recebido."""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> Any:
        self.statements.append(statement)

        class _Result:
            def scalars(self) -> list[Any]:
                return []

            def scalar_one_or_none(self) -> None:
                return None

        return _Result()


def _sql(statement: Any) -> str:
    return str(statement.compile(compile_kwargs={"literal_binds": True}))


class TestTenantReadsAreFailClosed:
    """O filtro tem de estar no SQL - filtrar depois da leitura vaza."""

    @pytest.mark.asyncio
    async def test_get_by_id_and_tenant_filtra_no_sql(self) -> None:
        from app.services.incident_repository import IncidentRepository

        session = _CapturingSession()
        repo = IncidentRepository(session)  # type: ignore[arg-type]
        incident_id = "00000000-0000-4000-8000-000000000004"

        result = await repo.get_by_id_and_tenant(incident_id, "acme")

        assert result is None
        sql = _sql(session.statements[0])
        assert "incidents.tenant_id" in sql
        assert "acme" in sql
        # UUID e' renderizado sem hifens pelo dialeto generico.
        assert incident_id.replace("-", "") in sql.replace("-", "")

    @pytest.mark.asyncio
    async def test_filter_by_tenant_id_filtra_no_sql(self) -> None:
        from app.services.incident_repository import IncidentRepository

        session = _CapturingSession()
        repo = IncidentRepository(session)  # type: ignore[arg-type]

        await repo.filter_by_tenant_id("acme")

        sql = _sql(session.statements[0])
        assert "incidents.tenant_id" in sql
        assert "acme" in sql

    @pytest.mark.asyncio
    async def test_list_recent_com_tenant_filtra_no_sql(self) -> None:
        """`list_recent` sem tenant e' a rota permissiva - documentada como
        tal; com tenant, o filtro precisa existir no SQL."""
        from app.services.incident_repository import IncidentRepository

        session = _CapturingSession()
        repo = IncidentRepository(session)  # type: ignore[arg-type]

        await repo.list_recent(tenant_id="acme")

        sql = _sql(session.statements[0])
        assert "incidents.tenant_id" in sql
        assert "acme" in sql
