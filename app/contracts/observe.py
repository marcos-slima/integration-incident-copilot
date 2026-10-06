"""DA-52: orquestracao do detector de drift.

Fluxo de uma observacao:

    conector.fetch_contract()      -> Contract | None      (app/contracts)
    baseline mais recente          -> Contract | None      (system_contracts)
    diff_contracts(before, after)  -> DriftReport          (puro)
    persiste a observacao          -> system_contracts
    breaking?                      -> CloudEvent -> run_diagnosis

O sinal entra pelo event mesh que JA existe (DA-23: `IncidentEventEnvelope`
-> `handle_incident_event` -> `run_diagnosis`) em vez de um caminho novo.
Nao e' acerto porreuse: um detector que abre incidente por um caminho
proprietario teria dois lugares diferente para observar o mesmo incidente, e
o incidente de drift nao apareceria nos mesmos dashboards, na mesma tabela e
na mesma correlacao por `system_key` (DA-50) dos demais. Reusando o mesh, o
drift chega ao painel e' um incidente como qualquer outro.

E `connector_source_system` leva o `system_key` do catalogo, nao um rotulo
livre tipo "OData". E' o que garante a correlacao exata da DA-50/invariante
12: com `system_key`, a resolucao e' por igualdade e nao ha ambiguidade a
resolver.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import settings
from app.contracts.baseline import SystemContract, latest_contract
from app.contracts.diff import (
    SEVERITY_BREAKING,
    DriftReport,
    ObservationStatus,
    diff_contracts,
    unverified_report,
)
from app.contracts.model import Contract
from app.contracts.odata import contract_from_dict
from app.db import get_sync_session_factory
from app.models import IncidentEventData

_logger = logging.getLogger(__name__)

#: Quantas linhas de mudanca entram na descricao do incidente. O texto
#: alimenta o LLM (evidencia) e um payload de 400 campos quebraria o
#: `MAX_DESCRIPTION_LENGTH` e o orcamento de contexto (DA-25) sem tornar a
#: causa mais clara. O resto fica no report completo.
MAX_CHANGES_IN_EVENT = 12


def _session_factory() -> Any:
    """Fabrica sync compartilhada (app/db.py). `None` sem DATABASE_URL -- e
    os chamadores ja tratam isso como "sem persistencia", nao como erro."""
    return get_sync_session_factory()


def describe_report(report: DriftReport) -> str:
    """Texto que vira descricao do incidente. Escrito para humano E para o
    LLM: nomeia o sistema, o campo e o antes/depois, sem adjetivo."""
    if report.status is ObservationStatus.UNVERIFIED:
        return f"Contrato de {report.system_key} nao pode ser verificado: {report.reason}"
    if report.status is ObservationStatus.FIRST_OBSERVATION:
        return f"Contrato de {report.system_key} observado pela primeira vez; baseline criado"
    if report.status is ObservationStatus.CLEAN:
        return f"Contrato de {report.system_key} inalterado"
    breaking = [c for c in report.changes if c.severity == SEVERITY_BREAKING]
    lines = [
        (
            f"Drift de contrato ({report.severity}) em {report.system_key} "
            f"[{report.kind or 'contrato'}]: {len(breaking)} mudanca(s) incompativel(is)."
        )
    ]
    for change in report.changes[:MAX_CHANGES_IN_EVENT]:
        marker = "BREAKING" if change.severity == SEVERITY_BREAKING else change.severity.upper()
        lines.append(f"[{marker}] {change.describe()}")
        if change.hint:
            lines.append(f"  hint: {change.hint}")
    if len(report.changes) > MAX_CHANGES_IN_EVENT:
        lines.append(f"... e mais {len(report.changes) - MAX_CHANGES_IN_EVENT} mudanca(s)")
    return "\n".join(lines)


def to_incident_data(
    report: DriftReport,
    *,
    connector_type: str,
) -> IncidentEventData | None:
    """Converte um report em evento de incidente, ou `None`.

    `None` e' a resposta para clean, unverified, first_observation e
    additive. So `breaking` vira incidente. Drift aditivo muda o orcamento
    de contexto mas nao quebra ninguem; abrir incidente para ele seria o
    caminho rapido para o time desligar o detector.
    """
    if report.status is not ObservationStatus.DRIFT or not report.is_breaking:
        return None
    return IncidentEventData(
        description=describe_report(report),
        # O report inteiro em `logs`: o consumidor do incidente precisa
        # poder reconstruir o comparativo, e a description foi truncada.
        logs=json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True),
        interface_type=connector_type,  # type: ignore[arg-type]
        # DA-50: `system_key` do catalogo, para correlacao exata. Nao o
        # rotulo do conector, que e' livre e produziria 'ambiguo'.
        connector_source_system=report.system_key,
        sensitivity_level="internal",
    )


def observe(
    *,
    system_key: str,
    contract: Contract | None,
    connector_type: str,
    failure_reason: str | None = None,
) -> DriftReport:
    """Compara o contrato observado com o baseline, EMITE incidente (breaking)
    e DEPOIS persiste a observacao.

    Persiste mesmo em `first_observation` e `clean`: e' o que cria o
    baseline e o que mantem o historico de "quando mudou". Nao persiste em
    `unverified`, porque nao ha contrato novo -- gravar o vazio apagaria o
    baseline anterior sem ganho nenhum e o proximo diff compararia contra
    nada.

    **Ordem critical**: emite CloudEvent de incidente ANTES de gravar baseline.
    Se a entrega falhar, a persistencia ainda ocorre. O contrario perderia o
    sinal para sempre (baseline gravado mas evento nunca emitido).
    """
    if contract is None:
        return unverified_report(system_key, failure_reason or "contrato indisponivel")

    report = diff_contracts(_load_baseline(system_key), contract)
    report = DriftReport(
        system_key=system_key,
        status=report.status,
        severity=report.severity,
        changes=report.changes,
        fingerprint_before=report.fingerprint_before,
        fingerprint_after=report.fingerprint_after,
        reason=report.reason,
        kind=report.kind,
    )
    _emit_incident_and_persist(report, contract, connector_type=connector_type)
    return report


def _load_baseline(system_key: str) -> Contract | None:
    if not settings.database_url:
        return None
    try:
        with _session_factory()() as session:
            row: SystemContract | None = latest_contract(session, system_key)
            if row is None:
                return None
            return contract_from_dict(row.contract or {})
    except Exception:
        # Sem baseline nao e' "sem drift": vira first_observation quando o
        # contrato chegar, e o report carrega isso explicitamente. Um erro
        # de banco nao pode virar um "esta tudo bem" silencioso.
        _logger.exception("[contracts] falha ao ler baseline de %s", system_key)
        return None


def _persist(report: DriftReport, contract: Contract, *, connector_type: str) -> None:
    if not settings.database_url:
        return
    try:
        with _session_factory()() as session:
            session.add(
                SystemContract(
                    id=uuid.uuid4(),
                    system_key=report.system_key,
                    connector_type=connector_type,
                    contract_kind=contract.kind,
                    fingerprint=contract.fingerprint(),
                    contract=dict(contract.as_dict()),
                    entity_count=len(contract.entities),
                    property_count=contract.property_count(),
                    observation_status=report.status.value,
                    observed_at=datetime.now(UTC),
                )
            )
            session.commit()
    except Exception:
        # Best-effort, como o incident_recorder: perder a persistencia do
        # report NAO pode fazer o detector levantar e derrubar o polling.
        # O diagnostico ja foi emitido; o log deixa o buraco visivel.
        _logger.exception("[contracts] falha ao gravar observacao de %s", report.system_key)


def _emit_incident_and_persist(
    report: DriftReport, contract: Contract, *, connector_type: str
) -> None:
    """Se breaking, emite incidente E DEPOIS persiste baseline.

    Garante que o incidente seja enviado ANTES de gravar o baseline no banco.
    Se a emissao falhar, o baseline ainda e' gravado (a observacao e' valida,
    mesmo que o incidente NAO foi entregue).

    O contrario (persistir antes) perde o incidente para sempre se o banco
    gravar mas o event mesh falhar: baseline atualizado mas sinal nunca
    chegou ao consumidor.
    """
    # Emite incidente PRIMEIRO. So breaking vira incidente.
    # A emissao pode falhar, mas isso NAO deve impedir a persistencia.
    has_incident = emit_incident(report, connector_type=connector_type)
    _logger.info(
        "[contracts] incidente emitido com sucesso: %s",
        report.system_key,
    ) if has_incident else None
    # Depois persiste o baseline (mesmo para first_observation e clean).
    # Best-effort: falha de persistencia eh logada mas nao interrompe o flow.
    _persist(report, contract, connector_type=connector_type)


def emit_incident(report: DriftReport, *, connector_type: str) -> bool:
    """Se o report for breaking, entrega o evento ao event mesh (DA-23).

    **Contrato de retorno, e a confusao que ele nao esconde.** `True`
    quando houve emissao. `False` cobre DOIS casos: "nao havia nada a
    emitir" (report limpo) e "a entrega falhou". Para o loop de polling a
    distincao e' irrelevante — os dois sao nao-incidentes e o erro ja foi
    logado. Mas quem mostra o resultado a um humano precisa saber qual dos
    dois foi, senao a mensagem aponta o operador para a causa errada (foi
    exatamente o que aconteceu na primeira versao do CLI, que respondia
    "--no-emit?" mesmo quando a falha era de rede).

    Como recuperar a distincao: `False` + `report.is_breaking` implica
    falha na entrega, porque o caminho "nada a fazer" so' ocorre com report
    nao-breaking. Quem precisar de um sinal explicito em vez dessa
    inferencia deve trocar o `bool` por um enum de duas posicoes.
    """
    data = to_incident_data(report, connector_type=connector_type)
    if data is None:
        return False
    try:
        from app.events.consumer import handle_incident_event
        from app.models import INCIDENT_DETECTED_EVENT_TYPE, IncidentEventEnvelope

        handle_incident_event(
            IncidentEventEnvelope(
                type=INCIDENT_DETECTED_EVENT_TYPE,
                source="schema-drift-detector",
                # id unico por observacao: CloudEvents exige, e replay
                # do mesmo evento tem de ser rastreavel ate a observacao
                # que o produziu.
                id=f"drift-{report.system_key}-{report.fingerprint_after}",
                time=datetime.now(UTC).isoformat(),
                data=data,
            )
        )
        _logger.info(
            "[contracts] incidente de drift criado: %s (system_key=%s)",
            report.severity,
            report.system_key,
        )
    except Exception:
        _logger.exception("[contracts] drift breaking em %s nao virou incidente", report.system_key)
        return False
    return True


def check_connector(
    connector: Any,
    *,
    system_key: str,
    connector_type: str,
) -> DriftReport:
    """Faz a observacao completa de um conector: probe -> diff -> persiste.

    Uma observacao, uma transacao. Nao ha leitura de `$metadata` aqui: a
    introspeccao e' responsabilidade do conector, que e' quem tem a
    credencial (e quem ja sabe falar com o SAP).
    """
    try:
        contract = connector.fetch_contract()
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as exc:
        # `fetch_contract` ja engole erro de transporte e devolve None; o
        # que escapa aqui e' erro de uso (cliente injetado errado, config
        # ausente). estreito de proposito: um `except Exception` aqui
        # transformaria bug em "nao introspectavel", que e' um estado que
        # ninguem investiga.
        return observe(
            system_key=system_key,
            contract=None,
            connector_type=connector_type,
            failure_reason=f"{type(exc).__name__}: {exc}",
        )
    return observe(system_key=system_key, contract=contract, connector_type=connector_type)
