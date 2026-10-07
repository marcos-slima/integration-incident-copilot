"""Métricas Prometheus para o Integration Incident Copilot (Fase 2 Observabilidade).

Expõe métricas customizadas além das automáticas do FastAPI, segmentadas
pelas três audiências da stack de observabilidade:

    COI/IOC  — volume de diagnósticos, latência, taxa de acerto
    SOC      — PII detectado, redação aplicada, incidentes sensíveis
    iPaaS    — saúde de conectores, circuit breaker, erros por código

Uso (já wired em app/main.py via setup_metrics):
    from app.metrics import setup_metrics, DIAGNOSIS_COUNTER, ...

/metrics só é exposto quando PROMETHEUS_ENABLED=true **e** METRICS_TOKEN
está preenchido (Authorization: Bearer). Validação 2026-10-07 (M-14):
`setup_metrics` nunca era chamado (o /metrics dava 404), seis das onze
métricas nunca eram incrementadas e `iic_diagnosis_total` usava o FLOAT
`evidence_strength` como label (uma série nova por valor). Agora o label é a
faixa (`observe_diagnosis`) e todas as métricas têm ponto de incremento.

Design decisions:
- prometheus_fastapi_instrumentator instrumenta automaticamente todos os
  endpoints FastAPI com métricas HTTP padrão (REQUEST_COUNT, LATENCY).
  As métricas customizadas abaixo complementam com semântica de negócio.
- Labels mantidos pequenos (<10 valores por label) para evitar cardinalidade
  alta que degrada performance do Prometheus/Grafana em séries temporais.
- Prefixo `iic_` (Integration Incident Copilot) evita colisão com métricas
  da plataforma Kyma/Kubernetes.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Imports condicionais (sem quebrar startup se lib ausente)
# ---------------------------------------------------------------------------

try:
    from prometheus_client import Counter, Gauge, Histogram
    from prometheus_fastapi_instrumentator import Instrumentator

    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False
    Counter = Gauge = Histogram = Instrumentator = None  # type: ignore[misc, assignment]

# ---------------------------------------------------------------------------
# Métricas customizadas (COI/IOC, SOC, iPaaS)
# ---------------------------------------------------------------------------

if _PROMETHEUS_AVAILABLE:
    # COI/IOC — volume e qualidade de diagnósticos
    DIAGNOSIS_TOTAL = Counter(
        "iic_diagnosis_total",
        "Total de diagnósticos processados",
        # M-14: faixa discreta (none/low/medium/high), nunca o float.
        labelnames=["agent_domain", "llm_provider", "evidence_level"],
    )

    DIAGNOSIS_LATENCY = Histogram(
        "iic_diagnosis_latency_seconds",
        "Latência do pipeline de diagnóstico (segundos)",
        labelnames=["agent_domain", "llm_provider"],
        buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, float("inf")),
    )

    DIAGNOSIS_VERIFIED_TOTAL = Counter(
        "iic_diagnosis_verified_total",
        "Total de diagnósticos verificados por humanos",
        labelnames=["agent_domain", "verdict"],  # verdict: correct | incorrect
    )

    # SOC — segurança e privacidade
    PII_DETECTED_TOTAL = Counter(
        "iic_pii_detected_total",
        "Total de incidentes onde PII foi detectado",
        labelnames=["sensitivity_level"],
    )

    REDACTION_APPLIED_TOTAL = Counter(
        "iic_redaction_applied_total",
        "Total de incidentes onde redação de PII foi aplicada",
        labelnames=["sensitivity_level"],
    )

    SENSITIVE_INCIDENT_TOTAL = Counter(
        "iic_sensitive_incident_total",
        "Total de incidentes classificados como confidential ou secret",
        labelnames=["sensitivity_level"],
    )

    # iPaaS — saúde de conectores e circuit breaker
    CONNECTOR_REQUEST_TOTAL = Counter(
        "iic_connector_request_total",
        "Total de chamadas a conectores externos",
        labelnames=["connector", "status"],  # status: success | error | mock
    )

    CIRCUIT_BREAKER_OPEN_TOTAL = Counter(
        "iic_circuit_breaker_open_total",
        "Número de vezes que o circuit breaker abriu por provider/connector",
        labelnames=["target"],  # target: nome do provider ou conector
    )

    CIRCUIT_BREAKER_STATE = Gauge(
        "iic_circuit_breaker_state",
        "Estado atual do circuit breaker (0=fechado, 1=aberto)",
        labelnames=["target"],
    )

    LLM_FALLBACK_TOTAL = Counter(
        "iic_llm_fallback_total",
        "Total de ativações do fallback híbrido de LLM",
        labelnames=["primary_provider", "fallback_provider"],
    )

    RULE_ENGINE_HIT_TOTAL = Counter(
        "iic_rule_engine_hit_total",
        "Total de diagnósticos resolvidos pelo Rule Engine (sem LLM)",
        labelnames=["rule_id"],
    )

else:
    # Stubs para quando prometheus_client não está instalado
    class _NullMetric:
        def labels(self, **_kw):
            return self

        def inc(self, *_a, **_kw):
            pass

        def observe(self, *_a, **_kw):
            pass

        def set(self, *_a, **_kw):
            pass

    _null = _NullMetric()
    DIAGNOSIS_TOTAL = _null  # type: ignore[assignment]
    DIAGNOSIS_LATENCY = _null  # type: ignore[assignment]
    DIAGNOSIS_VERIFIED_TOTAL = _null  # type: ignore[assignment]
    PII_DETECTED_TOTAL = _null  # type: ignore[assignment]
    REDACTION_APPLIED_TOTAL = _null  # type: ignore[assignment]
    SENSITIVE_INCIDENT_TOTAL = _null  # type: ignore[assignment]
    CONNECTOR_REQUEST_TOTAL = _null  # type: ignore[assignment]
    CIRCUIT_BREAKER_OPEN_TOTAL = _null  # type: ignore[assignment]
    CIRCUIT_BREAKER_STATE = _null  # type: ignore[assignment]
    LLM_FALLBACK_TOTAL = _null  # type: ignore[assignment]
    RULE_ENGINE_HIT_TOTAL = _null  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Setup no lifespan do FastAPI
# ---------------------------------------------------------------------------


def setup_metrics(app: FastAPI) -> None:
    """Instrumenta o app FastAPI com Prometheus (se habilitado).

    Chamado em app/main.py durante o lifespan ou no nível de módulo
    (antes do primeiro request).

    PROMETHEUS_ENABLED=false (default) → noop, sem /metrics endpoint.
    PROMETHEUS_ENABLED=true → /metrics expõe métricas HTTP padrão +
    métricas de negócio iic_*.
    """
    from app.config import settings

    if not getattr(settings, "prometheus_enabled", False):
        logger.debug("Prometheus desabilitado (PROMETHEUS_ENABLED=false).")
        return

    if not _PROMETHEUS_AVAILABLE:
        logger.warning(
            "PROMETHEUS_ENABLED=true mas prometheus-fastapi-instrumentator "
            "não está instalado. Adicione ao pyproject.toml e reinstale."
        )
        return

    if not settings.metrics_token:
        logger.warning(
            "PROMETHEUS_ENABLED=true sem METRICS_TOKEN: /metrics NAO exposto "
            "(metricas operacionais nao ficam publicas). Defina METRICS_TOKEN e "
            "configure o scrape com 'authorization: credentials: <token>'."
        )
        return

    import secrets

    from fastapi import Depends, Header, HTTPException

    # Anotacao so com tipos embutidos: o modulo usa `from __future__ import
    # annotations`, e um `Request` importado aqui dentro nao seria resolvido
    # pelo FastAPI (viraria parametro de query).
    def _require_metrics_token(authorization: str = Header(default="")) -> None:
        header = authorization or ""
        token = header[7:] if header.lower().startswith("bearer ") else ""
        if not secrets.compare_digest(token, settings.metrics_token):
            raise HTTPException(status_code=401, detail="token de metricas invalido/ausente")

    Instrumentator(
        should_group_status_codes=False,
        excluded_handlers=["/health", "/ready", "/metrics"],
    ).instrument(app).expose(
        app,
        endpoint="/metrics",
        include_in_schema=False,
        dependencies=[Depends(_require_metrics_token)],
    )

    logger.info("Prometheus /metrics endpoint habilitado (com token).")


def evidence_level(strength: float | None) -> str:
    """M-14: faixa de evidence_strength para label de metrica (4 valores)."""
    if strength is None:
        return "none"
    if strength >= 0.7:
        return "high"
    if strength >= 0.4:
        return "medium"
    return "low"


def observe_diagnosis(
    *,
    agent_domain: str | None,
    llm_provider: str | None,
    evidence_strength: float | None,
    latency_seconds: float,
    sensitivity_level: str | None,
    pii_detected: bool,
) -> None:
    """M-14: metricas de negocio de um diagnostico concluido.

    Nunca levanta: metrica e observacao, nao pode derrubar o diagnostico."""
    try:
        domain = agent_domain or "unknown"
        provider = llm_provider or "none"
        DIAGNOSIS_TOTAL.labels(
            agent_domain=domain,
            llm_provider=provider,
            evidence_level=evidence_level(evidence_strength),
        ).inc()
        DIAGNOSIS_LATENCY.labels(agent_domain=domain, llm_provider=provider).observe(
            latency_seconds
        )
        level = sensitivity_level or "unknown"
        # A descricao sempre passa por redact_pii_text antes de persistir.
        REDACTION_APPLIED_TOTAL.labels(sensitivity_level=level).inc()
        if pii_detected:
            PII_DETECTED_TOTAL.labels(sensitivity_level=level).inc()
        if level in {"confidential", "secret"}:
            SENSITIVE_INCIDENT_TOTAL.labels(sensitivity_level=level).inc()
    except Exception:
        logger.debug("metricas do diagnostico nao registradas", exc_info=True)
