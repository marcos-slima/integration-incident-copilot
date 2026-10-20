"""DA-89: Relatórios compliance (export PDF/XLSX).

Fornecidas:
- build_compliance_summary
- calculate_quality_metrics
- extract_auditable_events
- get_compliance_data
- export_pdf
- export_xlsx
"""

import io
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.incident_repository import IncidentRepository

#: Media types usados pelas rotas de exportação (DA-89).
PDF_MEDIA_TYPE = "application/pdf"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _domain_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Normaliza as métricas por domínio para uma lista de linhas.

    Aceita tanto ``{"domains": [...]}`` (saída de ``build_dashboard_data``)
    quanto ``{"by_domain": {"sap": {...}, ...}}`` (saída do relatório de
    compliance).
    """
    domains = data.get("domains")
    if isinstance(domains, list):
        return domains

    by_domain = data.get("by_domain") or {}
    rows: list[dict[str, Any]] = []
    for domain, stats in by_domain.items():
        stats = stats or {}
        rows.append(
            {
                "domain": domain,
                "count": stats.get("count", 0),
                "hit1": stats.get("accuracy", stats.get("hit1", 0.0)),
                "latency_ms": stats.get("latency_ms", 0.0),
                "cost_usd": stats.get("cost_usd", 0.0),
            }
        )
    return rows


def build_compliance_summary(incidents: list[dict[str, Any]]) -> dict[str, Any]:
    """Constrói resumo de compliance para relatório.

    Args:
        incidents: Lista de incidentes

    Returns:
        Dict com resumo de incidentes, custos e métricas de qualidade
    """
    total = len(incidents)
    verified = [i for i in incidents if i.get("diagnosis_correct") is not None]
    verified_count = len(verified)
    correct_count = sum(1 for i in verified if i.get("diagnosis_correct") is True)

    # Custo total
    total_cost = sum(i.get("cost_usd") or 0.0 for i in incidents)

    # Por domínio
    by_domain: dict[str, dict[str, int]] = {}
    for i in incidents:
        domain = i.get("agent_domain", "generic")
        if domain not in by_domain:
            by_domain[domain] = {"count": 0, "correct": 0}
        by_domain[domain]["count"] += 1
        if i.get("diagnosis_correct") is True:
            by_domain[domain]["correct"] += 1

    return {
        "total_incidents": total,
        "verified_correctly": correct_count,
        "verification_rate": verified_count / total if total > 0 else 0.0,
        "cost_usd_total": round(total_cost, 6),
        "by_domain": {
            domain: {
                "count": stats["count"],
                "accuracy": stats["correct"] / stats["count"] if stats["count"] > 0 else 0.0,
            }
            for domain, stats in by_domain.items()
        },
    }


def calculate_quality_metrics(incidents: list[dict[str, Any]]) -> dict[str, Any]:
    """Calcula métricas de qualidade para o relatório.

    Args:
        incidents: Lista de incidentes

    Returns:
        Dict com métricas de qualidade
    """
    verified = [i for i in incidents if i.get("diagnosis_correct") is not None]
    total_verified = len(verified)
    correct_count = sum(1 for i in verified if i.get("diagnosis_correct") is True)

    # Accuracy geral
    overall_accuracy = correct_count / total_verified if total_verified > 0 else 0.0

    # Por domínio
    domain_stats: dict[str, dict[str, int]] = {}
    for i in incidents:
        domain = i.get("agent_domain", "generic")
        if domain not in domain_stats:
            domain_stats[domain] = {"verified": 0, "correct": 0}

        if i.get("diagnosis_correct") is not None:
            domain_stats[domain]["verified"] += 1
            if i.get("diagnosis_correct") is True:
                domain_stats[domain]["correct"] += 1

    by_domain: dict[str, dict[str, float]] = {}
    for domain, stats in domain_stats.items():
        by_domain[domain] = {
            "accuracy": stats["correct"] / stats["verified"] if stats["verified"] > 0 else 0.0,
            "verification_rate": stats["verified"] / len(incidents) if incidents else 0.0,
        }

    # Taxa de verificação (incidentes verificados / total)
    verification_rate = total_verified / len(incidents) if incidents else 0.0

    return {
        "overall_accuracy": round(overall_accuracy, 4),
        "by_domain": by_domain,
        "verification_rate": round(verification_rate, 4),
        "total_incidents": len(incidents),
        "total_verified": total_verified,
    }


def extract_auditable_events(incidents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extrai eventos auditáveis do histórico de incidentes.

    Args:
        incidents: Lista de incidentes

    Returns:
        Lista de eventos auditáveis (diagnósticos e verificações)
    """
    events = []

    for incident in incidents:
        # Evento: diagnóstico criado
        events.append(
            {
                "incident_id": str(incident.get("id", "")),
                "timestamp": incident.get("created_at"),
                "action": "diagnosis_created",
                "agent_domain": incident.get("agent_domain"),
                "probable_root_cause": incident.get("probable_root_cause"),
                "model_confidence": incident.get("model_confidence"),
            }
        )

        # Evento: verificação (se existir)
        if incident.get("verified_at"):
            events.append(
                {
                    "incident_id": str(incident.get("id", "")),
                    "timestamp": incident.get("verified_at"),
                    "action": "verification_completed",
                    "diagnosis_correct": incident.get("diagnosis_correct"),
                    "verified_by": incident.get("verified_by"),
                }
            )

    # Ordena por timestamp (mais recente primeiro)
    events.sort(key=lambda e: str(e.get("timestamp") or ""), reverse=True)

    return events


async def get_compliance_data(session: AsyncSession | None, days: int = 7) -> dict[str, Any]:
    """Busca dados de compliance do banco.

    Args:
        session: Sessão async do SQLAlchemy (``None`` quando não há banco)
        days: Janela de dias declarada no relatório

    Returns:
        Dict com dados de compliance
    """
    if session is None:
        return {"incidents": [], "error": "database_not_configured", "period_days": days}

    try:
        repo = IncidentRepository(session)
        incidents = await repo.list_recent(limit=1000)

        incidents_data = [
            {
                "id": str(i.id),
                "created_at": i.created_at.isoformat() if i.created_at else None,
                "agent_domain": i.agent_domain,
                "probable_root_cause": i.probable_root_cause,
                "model_confidence": i.model_confidence,
                "diagnosis_correct": i.diagnosis_correct,
                "verified_at": i.verified_at.isoformat() if i.verified_at else None,
                "verified_by": getattr(i, "verified_by", None),
                "cost_usd": i.cost_usd,
                "latency_ms": i.latency_ms,
            }
            for i in incidents
        ]

        return {
            "incidents": incidents_data,
            "period_days": days,
            "generated_at": datetime.now(UTC).isoformat(),
        }
    except Exception as e:  # noqa: BLE001 - falha de banco vira 503 na rota
        return {"incidents": [], "error": str(e), "period_days": days}


def export_pdf(data: dict[str, Any]) -> bytes:
    """Exporta dados de compliance para PDF.

    Args:
        data: Dict com dados de compliance

    Returns:
        Bytes do PDF gerado
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()

    # pageCompression=0 mantém o texto legível nos bytes (auditoria/diff).
    doc = SimpleDocTemplate(buffer, pagesize=letter, pageCompression=0)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Compliance Report - Integration Incident Copilot", styles["Title"]))
    story.append(Spacer(1, 12))

    summary = data.get("summary", {})
    story.append(
        Paragraph(f"Total de Incidentes: {summary.get('total_incidents', 0)}", styles["Normal"])
    )
    story.append(
        Paragraph(f"Custo Total: ${summary.get('cost_usd_total', 0):.6f}", styles["Normal"])
    )
    metrics = data.get("metrics", {})
    if "overall_accuracy" in metrics:
        story.append(
            Paragraph(
                f"Accuracy Geral (Hit@1): {metrics.get('overall_accuracy', 0):.4f}",
                styles["Normal"],
            )
        )
    if summary.get("verification_rate") is not None:
        story.append(
            Paragraph(
                f"Taxa de Verificação: {summary.get('verification_rate', 0):.4f}",
                styles["Normal"],
            )
        )
    story.append(Spacer(1, 12))

    story.append(Paragraph("Métricas por Domínio", styles["Heading2"]))
    story.append(Spacer(1, 6))

    data_rows = [["Domínio", "Count", "Hit@1", "Latency(ms)", "Cost($)"]]
    for domain in _domain_rows(data):
        data_rows.append(
            [
                str(domain.get("domain", "")),
                str(domain.get("count", 0)),
                f"{domain.get('hit1', 0):.4f}",
                f"{domain.get('latency_ms', 0):.2f}",
                f"{domain.get('cost_usd', 0):.6f}",
            ]
        )

    table = Table(data_rows)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ]
        )
    )

    story.append(table)
    doc.build(story)
    buffer.seek(0)

    return buffer.getvalue()


def export_xlsx(data: dict[str, Any]) -> bytes:
    """Exporta dados de compliance para XLSX.

    Args:
        data: Dict com dados de compliance

    Returns:
        Bytes do XLSX gerado
    """
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Compliance Report"

    headers = ["Domínio", "Count", "Hit@1", "Latency(ms)", "Cost($)"]
    ws.append(headers)

    for domain in _domain_rows(data):
        ws.append(
            [
                str(domain.get("domain", "")),
                domain.get("count", 0),
                domain.get("hit1", 0),
                domain.get("latency_ms", 0),
                domain.get("cost_usd", 0),
            ]
        )

    summary = data.get("summary", {})
    ws.append([])
    ws.append(["Resumo Geral"])
    ws.append(["Total Incidentes", summary.get("total_incidents", 0)])
    ws.append(["Custo Total ($)", summary.get("cost_usd_total", 0)])
    if "overall_accuracy" in data.get("metrics", {}):
        ws.append(["Accuracy Geral (Hit@1)", data["metrics"]["overall_accuracy"]])

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    return buffer.getvalue()
