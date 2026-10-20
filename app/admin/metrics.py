"""DA-87: Métricas por domínio (sap/saas/generic) para dashboards.

Fornecidas:
- count_incidents_by_domain
- calculate_domain_hit1
- calculate_domain_latency
- calculate_cost_by_domain
- build_dashboard_data
"""

from typing import Any


def count_incidents_by_domain(incidents: list[dict[str, Any]]) -> dict[str, int]:
    """Contagem de incidentes por domínio.

    Args:
        incidents: Lista de incidentes com campo agent_domain

    Returns:
        Dict com contagem por domínio (sap, saas, generic)
    """
    result = {"sap": 0, "saas": 0, "generic": 0}

    for incident in incidents:
        domain = incident.get("agent_domain", "generic")
        if domain in result:
            result[domain] += 1
        else:
            result["generic"] += 1

    return result


def calculate_domain_hit1(incidents: list[dict[str, Any]]) -> dict[str, float]:
    """Calcula Hit@1 (precisão de diagnosticos corretos) por domínio.

    Hit@1 = # verificados corretamente / # total verificados

    Args:
        incidents: Lista de incidentes com campos diagnosis_correct e agent_domain

    Returns:
        Dict com Hit@1 por domínio (0.0 a 1.0)
    """
    domain_stats: dict[str, dict[str, int]] = {
        "sap": {"correct": 0, "total": 0},
        "saas": {"correct": 0, "total": 0},
        "generic": {"correct": 0, "total": 0},
    }

    for incident in incidents:
        domain = incident.get("agent_domain", "generic")
        if domain not in domain_stats:
            domain = "generic"

        domain_stats[domain]["total"] += 1

        # Só conta como correto se foi verificado (diagnosis_correct não é None)
        if incident.get("diagnosis_correct") is True:
            domain_stats[domain]["correct"] += 1

    result: dict[str, float] = {}
    for domain, stats in domain_stats.items():
        if stats["total"] > 0:
            result[domain] = stats["correct"] / stats["total"]
        else:
            result[domain] = 0.0

    return result


def calculate_domain_latency(incidents: list[dict[str, Any]]) -> dict[str, float]:
    """Calcula tempo médio (latência) por domínio.

    Args:
        incidents: Lista de incidentes com campos latency_ms e agent_domain

    Returns:
        Dict com latência média (ms) por domínio
    """
    domain_latencies: dict[str, list[float]] = {
        "sap": [],
        "saas": [],
        "generic": [],
    }

    for incident in incidents:
        domain = incident.get("agent_domain", "generic")
        if domain not in domain_latencies:
            domain = "generic"

        latency = incident.get("latency_ms")
        if latency is not None:
            domain_latencies[domain].append(float(latency))

    result: dict[str, float] = {}
    for domain, latencies in domain_latencies.items():
        if latencies:
            result[domain] = sum(latencies) / len(latencies)
        else:
            result[domain] = 0.0

    return result


def calculate_cost_by_domain(incidents: list[dict[str, Any]]) -> dict[str, float]:
    """Calcula custo total por domínio.

    Args:
        incidents: Lista de incidentes com campos cost_usd e agent_domain

    Returns:
        Dict com custo total (USD) por domínio
    """
    result = {"sap": 0.0, "saas": 0.0, "generic": 0.0}

    for incident in incidents:
        domain = incident.get("agent_domain", "generic")
        if domain not in result:
            domain = "generic"

        cost = incident.get("cost_usd")
        if cost is not None:
            result[domain] += float(cost)

    return result


def build_dashboard_data(incidents: list[dict[str, Any]]) -> dict[str, Any]:
    """Constrói estrutura de dados para o dashboard de domínios.

    Args:
        incidents: Lista de incidentes

    Returns:
        Dict com métricas agrupadas por domínio e resumo
    """
    # Métricas por domínio
    domain_counts = count_incidents_by_domain(incidents)
    domain_hit1 = calculate_domain_hit1(incidents)
    domain_latency = calculate_domain_latency(incidents)
    domain_cost = calculate_cost_by_domain(incidents)

    # Constrói lista de domínios com todas as métricas
    domains_data = []
    for domain in ["sap", "saas", "generic"]:
        domains_data.append(
            {
                "domain": domain,
                "count": domain_counts.get(domain, 0),
                "hit1": round(domain_hit1.get(domain, 0.0), 4),
                "latency_ms": round(domain_latency.get(domain, 0.0), 2),
                "cost_usd": round(domain_cost.get(domain, 0.0), 6),
            }
        )

    # Resumo geral
    total_incidents = len(incidents)
    verified_incidents = [i for i in incidents if i.get("diagnosis_correct") is not None]
    total_verified = len(verified_incidents)
    correct_count = sum(1 for i in verified_incidents if i.get("diagnosis_correct") is True)
    overall_hit1 = correct_count / total_verified if total_verified > 0 else 0.0

    total_cost = sum(i.get("cost_usd") or 0.0 for i in incidents)
    avg_latency = (
        sum(i.get("latency_ms") or 0 for i in incidents) / total_incidents
        if total_incidents > 0
        else 0
    )

    return {
        "domains": domains_data,
        "total": total_incidents,
        "summary": {
            "verified_count": total_verified,
            "overall_hit1": round(overall_hit1, 4),
            "total_cost_usd": round(total_cost, 6),
            "avg_latency_ms": round(avg_latency, 2),
        },
    }
