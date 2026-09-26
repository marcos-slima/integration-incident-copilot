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
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from app.config import settings
from app.redaction import redact_pii_text

if TYPE_CHECKING:
    from app.models import DiagnosisResponse, IncidentRequest

_logger = logging.getLogger(__name__)


def _sync_url(raw: str) -> str:
    for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
        if raw.startswith(prefix):
            return "postgresql+psycopg2://" + raw[len(prefix) :]
    return raw


@lru_cache(maxsize=1)
def _get_session_factory():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        _sync_url(settings.database_url),
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=3,
        connect_args={"connect_timeout": 3},
    )
    return sessionmaker(engine, expire_on_commit=False)


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
    return {
        "id": uuid.UUID(incident_id),
        "trace_id": response.trace_id,
        "interface_type": request.interface_type,
        "description": redacted_description,
        "connector_source_system": getattr(connector, "source_system", None),
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
        "evidence_json": [e.model_dump(mode="json") for e in response.evidence] or None,
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
