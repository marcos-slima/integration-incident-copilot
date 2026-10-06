"""B-01: grava cada diagnostico concluido na tabela `incidents` (PostgreSQL).

Antes, IncidentRepository e as migrations existiam, mas nenhum fluxo da
API gravava nada - os relatorios (scripts/generate_reports.py) e os
dashboards Grafana liam uma tabela vazia.

Chamado por run_diagnosis() (app/agent/graph.py), o ponto comum a todos
os caminhos: /diagnose, worker RQ, webhook/AMQP de eventos, A2A e MCP.

Decisoes:
  - Driver sincrono (psycopg2, ja dependencia para o Alembic): run_diagnosis
    e sincrono e roda tanto no threadpool do FastAPI quanto no worker RQ -
    usar o engine async (asyncpg) daqui exigiria criar/gerenciar event
    loops por chamada, e conexoes do pool ficariam presas ao loop errado.
  - Best-effort: falha de banco e logada e NUNCA quebra o diagnostico - a
    persistencia e analitica, o diagnostico e o produto.
  - Mesmo id do diagnostico (incident_id gerado em run_diagnosis), para o
    registro SQL, o grafo Neo4j (DA-28) e o trace Langfuse se referirem ao
    mesmo incidente.
  - Descricao gravada ja com redacao de PII (redact_pii_text).
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from app.config import settings
from app.db import get_sync_session_factory
from app.redaction import redact_pii_text

if TYPE_CHECKING:
    from app.models import DiagnosisResponse, IncidentRequest

_logger = logging.getLogger(__name__)


def _encrypt_evidence(evidence: Any) -> str | None:
    """Cifra evidence_json usando Fernet (DA-47/DA-60)."""
    if evidence is None:
        return None
    try:
        from json import dumps

        from app.admin.crypto import _get_fernet

        payload = dumps(evidence, ensure_ascii=False)
        return _get_fernet().encrypt(payload.encode()).decode()
    except Exception:  # noqa: BLE001
        _logger.warning("[incident_recorder] Falha ao cifrar evidence_json, deixando em claro")
        return None


def _get_session_factory():
    """Sessionmaker sync compartilhado (DA-52 consolidou em app/db.py).

    Antes desta DA, esta funcao e a de app/admin/repository.py eram copias
    identicas do mesmo codigo. Duas copias nao divergem sozinhas, mas
    divergem na primeira edicao -- e uma delas tenderia a ficar para tras
    num patch de seguranca.
    """
    return get_sync_session_factory()


def build_incident_row(
    *,
    incident_id: str,
    request: IncidentRequest,
    response: DiagnosisResponse,
    final_state: dict[str, Any],
    latency_ms: int,
) -> dict[str, Any]:
    """Monta as colunas do registro - funcao pura, testavel sem banco."""
    from app.llm.gateway import classify_sensitivity

    connector = final_state.get("connector_data")
    redacted_description = redact_pii_text(request.description)
    # DA-50: connector_source_system tem DUAS fontes em ordem de prioridade:
    # 1. o que o cliente informou (IncidentRequest.connector_source_system,
    #    propagado no CopilotState por graph.py::run_diagnosis) - esse e o
    #    system_key do catalogo (integration_systems, DA-49), e e o que
    #    permite correlacionar incidente <-> sistema na superficie admin;
    # 2. o rotulo generico do conector ("OData", "SAP CAP", ...) - fallback
    #    para quando o cliente nao informou nada.
    # Prioridade invertida causaria perda da correlacao: sem o valor do
    # cliente, connector_source_system seria sempre um rotulo de marketing
    # e a correlacao por system_key seria sempre NULL.
    connector_source_system = final_state.get("connector_source_system") or getattr(
        connector, "source_system", None
    )
    return {
        "id": uuid.UUID(incident_id),
        "trace_id": response.trace_id,
        "interface_type": request.interface_type,
        "description": redacted_description,
        "connector_source_system": connector_source_system,
        "is_mock": bool(getattr(connector, "is_mock", False)),
        "sensitivity_level": classify_sensitivity(final_state),
        "pii_detected": redacted_description != (request.description or ""),
        "redaction_applied": True,
        "latency_ms": latency_ms,
        "error_codes": [connector.error_code] if getattr(connector, "error_code", None) else None,
        "probable_root_cause": response.probable_root_cause,
        "model_confidence": response.model_confidence,
        "diagnosis_confidence": response.diagnosis_confidence,
        "evidence_strength": response.evidence_strength,
        "llm_provider_used": response.llm_provider_used,
        "agent_domain": response.agent_domain,
        # DA-53: proveniencia de modelo e prompt. `response.llm_model` e' o
        # que o state carregou (o nome real, nao o rotulo da rota -- invariante
        # 8: modelo nao entra na tabela de rotas).
        "llm_model": response.llm_model,
        "prompt_version": response.prompt_version,
        "prompt_digest": response.prompt_digest,
        "evidence_json": _encrypt_evidence(
            [e.model_dump(mode="json") for e in response.evidence] or None
        ),
    }


def record_incident(**kwargs: Any) -> None:
    """Persiste o diagnostico (mesmos argumentos de build_incident_row).
    No-op sem DATABASE_URL; qualquer erro e logado e engolido."""
    if not settings.database_url:
        return
    try:
        from app.services.incident_repository import Incident

        row = build_incident_row(**kwargs)
        with _get_session_factory()() as session:
            session.add(Incident(**row))
            session.commit()
    except Exception:
        _logger.exception(
            "[incidents] Falha ao gravar incidente %s no PostgreSQL (diagnostico nao afetado)",
            kwargs.get("incident_id"),
        )


def record_verification(
    incident_id: str,
    *,
    diagnosis_correct: bool | None,
    verified_by: str | None = None,
    verified_root_cause: str | None = None,
) -> bool:
    """DA-50: grava a verificacao humana na tabela `incidents` (best-effort).

    Antes desta mudanca, POST /incidents/{id}/verify so escrevia no grafo
    Neo4j e no score do Langfuse - os dashboards Grafana e a tela
    /admin/incidents liam `verified_at`/`diagnosis_correct` de uma tabela
    que NUNCA era atualizada (a taxa de verificacao ficava sempre em zero).
    A verificacao e o unico sinal de feedback do operador sobre a qualidade
    do diagnostico; perder isso no banco analitico e perder o dado na
    principal fonte de leitura.

    `diagnosis_correct=None` significa "verificado sem veredito" e grava
    `verified_at` deixando `diagnosis_correct` NULL - coerente com
    VerifyIncidentRequest e com o que a UI/dashboard mostram como
    "verificado (sem veredito)". Coagir None para True inflaria a taxa de
    acuracia dos dashboards com casos que o operador nao julizou.

    Retorna True se gravou, False se nao havia banco, id invalido ou falha -
    nunca levanta: um veredito no grafo/Langfuse nao pode ser perdido
    porque o SQL estava fora do ar.
    """
    if not settings.database_url:
        return False
    try:
        parsed_id = uuid.UUID(incident_id)
    except ValueError:
        return False
    try:
        from datetime import UTC, datetime

        from sqlalchemy import update

        from app.services.incident_repository import Incident

        with _get_session_factory()() as session:
            result = session.execute(
                update(Incident)
                .where(Incident.id == parsed_id)
                .values(
                    verified_at=datetime.now(UTC),
                    diagnosis_correct=diagnosis_correct,
                    verified_by=verified_by,
                    verified_root_cause=verified_root_cause,
                )
            )
            session.commit()
        # rowcount 0 = o id nao existe na tabela (ex: incidente so gravado
        # no grafo, sem DATABASE_URL na epoca do diagnostico) - trata como
        # "nao gravado" para o endpoint poder avisar o operador.
        return result.rowcount > 0
    except Exception:
        _logger.exception(
            "[incidents] Falha ao gravar verificacao do incidente %s no PostgreSQL",
            incident_id,
        )
        return False
