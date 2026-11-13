"""DA-52: comparativo entre contratos e classificacao de severidade.

Regras de classificacao, e o porque de cada uma:

`breaking` - o consumidor que estava funcionando para de funcionar:
  entidade removida; propriedade removida; tipo alterado; `Nullable`
  false->true (algo que mandava null agora e' rejeitado); `MaxLength`
  diminuido; propriedade que era chave deixou de ser. Ablacao de tipo e'
  SEMPRE breaking, mesmo quando parece uma enlarging: na pratica
  clientes OData quebram ao encontrar o tipo diferente, e um "additive"
  falso aqui custa um incident silencioso.

`additive` - o consumidor continua funcionando, mas o contrato mudou:
  propriedade nova, `Nullable` false->true (passou a aceitar null),
  `MaxLength` maior, entidade nova. Vale registrar porque payload maior
  muda o orcamento de token/embedding do RAG (DA-25) mesmo sem breaking.

`cosmetic` - anotacao, `Version`, ordem, namespace. A normalizacao do
  contrato (model.py) DESCARTA esses campos de proposito (invariante 18:
  sem isso o SAP republicando o servico geraria drift todo dia), entao o
  diff atual nunca emite `cosmetic`: a constante fica reservada para quando
  o modelo passar a guardar anotacoes.

`probable_rename` - propriedade removida + propriedade nova com a MESMA
  assinatura (tipo, nulabilidade, tamanho, chave). SAP renomeia campo em
  migracao de S/4 com frequencia suficiente para valer a pena dizer "isto
  e' o mesmo campo com outro nome" em vez de dois alarmes. Vem como
  `breaking` com `hint`, porque o efeito no consumidor e' identico.

Nenhuma dessas decisoes consulta LLM.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from app.contracts.model import Contract, Property

SEVERITY_BREAKING = "breaking"
SEVERITY_ADDITIVE = "additive"
SEVERITY_COSMETIC = "cosmetic"
SEVERITY_NONE = "none"

#: Ordem de gravidade. `max` sobre este dicionario e' como o report resumido
#: escolhe o pior caso -- e' o que a UI/dashboard vai ler.
SEVERITY_ORDER: Mapping[str, int] = {
    SEVERITY_NONE: 0,
    SEVERITY_COSMETIC: 1,
    SEVERITY_ADDITIVE: 2,
    SEVERITY_BREAKING: 3,
}


class ObservationStatus(str, Enum):
    """Tres estados E UM, e o quarto e' o mais importante.

    `CLEAN` e' "comparei e nao mudou". `DRIFT` e' "comparei e mudou".
    `FIRST_OBSERVATION` e' "primeira vez que vejo esse sistema -- nao tenho
    com o que comparar". `UNVERIFIED` e' "tentei ler e nao consegui".

    Reduzir a tres estados (ou a dois, collando FIRST_OBSERVATION em CLEAN)
    e' o caminho classico para o dashboard mentir: a primeira leitura
    apareceria como "saudavel" e a falha de leitura como "sem drift". O
    baseline ausente nao e' atestado de saude, e' ausencia de informacao --
    mesma liacao do baseline de promptfoo na DA-51.
    """

    CLEAN = "clean"
    DRIFT = "drift"
    FIRST_OBSERVATION = "first_observation"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class Change:
    severity: str
    kind: str
    entity: str
    property: str | None = None
    old: str | None = None
    new: str | None = None
    hint: str | None = None

    def describe(self) -> str:
        target = f"{self.entity}.{self.property}" if self.property else self.entity
        if self.kind == "property_removed":
            return f"{target} removido (era {self.old})"
        if self.kind == "property_added":
            return f"{target} novo ({self.new})"
        if self.kind == "type_changed":
            return f"{target} mudou de tipo: {self.old} -> {self.new}"
        if self.kind == "nullability_tightened":
            return f"{target} passou a ser NOT nullable (antes aceitava null)"
        if self.kind == "nullability_relaxed":
            return f"{target} passou a aceitar null"
        if self.kind == "max_length_reduced":
            return f"{target} reduziu MaxLength: {self.old} -> {self.new}"
        if self.kind == "max_length_increased":
            return f"{target} aumentou MaxLength: {self.old} -> {self.new}"
        if self.kind == "max_length_added":
            return f"{target} passou a ter MaxLength: {self.new}"
        if self.kind == "key_relaxed":
            return f"{target} deixou de ser chave"
        if self.kind == "key_added":
            return f"{target} passou a ser chave"
        if self.kind == "abstract":
            return f"{target} passou a ser abstrata (nao pode mais ser instanciada)"
        if self.kind == "entity_removed":
            return f"entidade {target} removida"
        return f"{target}: {self.kind}"

    def as_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity,
            "kind": self.kind,
            "entity": self.entity,
            "property": self.property,
            "old": self.old,
            "new": self.new,
            "hint": self.hint,
        }


@dataclass(frozen=True)
class DriftReport:
    """Resultado de uma observacao. Imutavel: o report e' evidencia, e
    evidencia nao muda depois de emitida."""

    system_key: str
    status: ObservationStatus
    severity: str
    changes: tuple[Change, ...]
    fingerprint_before: str | None = None
    fingerprint_after: str | None = None
    reason: str | None = None
    kind: str | None = None
    # Preenchido por observe(): True = incidente entregue; False = era
    # breaking e a entrega falhou (ou foi pulada com emit=False); None =
    # nao havia o que emitir.
    incident_emitted: bool | None = None

    @property
    def is_breaking(self) -> bool:
        return self.severity == SEVERITY_BREAKING

    @property
    def counts(self) -> dict[str, int]:
        counts = {SEVERITY_BREAKING: 0, SEVERITY_ADDITIVE: 0, SEVERITY_COSMETIC: 0}
        for change in self.changes:
            if change.severity in counts:
                counts[change.severity] += 1
        return counts

    def summary(self) -> str:
        if self.status is ObservationStatus.UNVERIFIED:
            return f"{self.system_key}: nao verificado ({self.reason or 'sem motivo'})"
        if self.status is ObservationStatus.FIRST_OBSERVATION:
            return f"{self.system_key}: baseline criado, sem comparacao anterior"
        if self.status is ObservationStatus.CLEAN:
            return f"{self.system_key}: contrato inalterado"
        counts = self.counts
        return (
            f"{self.system_key}: drift {self.severity} "
            f"({counts[SEVERITY_BREAKING]} breaking, {counts[SEVERITY_ADDITIVE]} additive, "
            f"{counts[SEVERITY_COSMETIC]} cosmetic)"
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "system_key": self.system_key,
            "status": self.status.value,
            "severity": self.severity,
            "counts": self.counts,
            "fingerprint_before": self.fingerprint_before,
            "fingerprint_after": self.fingerprint_after,
            "reason": self.reason,
            "kind": self.kind,
            "changes": [change.as_dict() for change in self.changes],
        }


def _pair_renames(removed: Mapping[str, Property], added: Mapping[str, Property]) -> dict[str, str]:
    """Casa removida->nova pela assinatura completa. So quando a assinatura
    bate inteira: com tipo e tamanho iguais e' rename com alta confianca; se
    so o tipo batesse, seria adivinhacao -- e o preco de errar aqui e' um
    incident apontando para o campo errado."""
    pairs: dict[str, str] = {}
    used: set[str] = set()
    for old_name in sorted(removed):
        old_signature = removed[old_name].signature()
        for new_name in sorted(added):
            if new_name in used:
                continue
            if added[new_name].signature() == old_signature:
                pairs[old_name] = new_name
                used.add(new_name)
                break
    return pairs


def _diff_properties(entity_name: str, before: Property, after: Property) -> list[Change]:
    changes: list[Change] = []
    if before.type_name != after.type_name:
        # Qualquer troca de tipo e' breaking, mesmo 'para mais largo'.
        changes.append(
            Change(
                severity=SEVERITY_BREAKING,
                kind="type_changed",
                entity=entity_name,
                property=before.name,
                old=before.type_name,
                new=after.type_name,
                hint="clientes OData tendem a quebrar na troca de tipo; valide o consumidor antes de aceitar",
            )
        )
    before_nullable = before.nullable if before.nullable is not None else True
    after_nullable = after.nullable if after.nullable is not None else True
    if before_nullable and not after_nullable:
        changes.append(
            Change(
                severity=SEVERITY_BREAKING,
                kind="nullability_tightened",
                entity=entity_name,
                property=before.name,
                old="nullable",
                new="not nullable",
                hint="consumidor que mandava null agora recebe rejeicao",
            )
        )
    elif not before_nullable and after_nullable:
        changes.append(
            Change(
                severity=SEVERITY_ADDITIVE,
                kind="nullability_relaxed",
                entity=entity_name,
                property=before.name,
            )
        )
    before_max_len = before.max_length
    after_max_len = after.max_length
    if before_max_len is None:
        before_max_len = -1
    if after_max_len is None:
        after_max_len = -1
    if before_max_len != -1 and after_max_len != -1:
        if after_max_len < before_max_len:
            changes.append(
                Change(
                    severity=SEVERITY_BREAKING,
                    kind="max_length_reduced",
                    entity=entity_name,
                    property=before.name,
                    old=str(before_max_len),
                    new=str(after_max_len),
                )
            )
        elif after_max_len > before_max_len:
            changes.append(
                Change(
                    severity=SEVERITY_ADDITIVE,
                    kind="max_length_increased",
                    entity=entity_name,
                    property=before.name,
                    old=str(before_max_len),
                    new=str(after_max_len),
                )
            )
    elif before_max_len == -1 and after_max_len != -1:
        # Sem MaxLength -> com MaxLength e' RESTRICAO (antes cabia qualquer
        # tamanho): o consumidor que mandava texto longo passa a ser
        # rejeitado. Mesma natureza de max_length_reduced (validacao
        # 2026-10-07, DATA-01).
        changes.append(
            Change(
                severity=SEVERITY_BREAKING,
                kind="max_length_added",
                entity=entity_name,
                property=before.name,
                old="null",
                new=str(after_max_len),
                hint="campo antes sem limite de tamanho; valores maiores passam a ser rejeitados",
            )
        )
    if before.key and not after.key:
        changes.append(
            Change(
                severity=SEVERITY_BREAKING,
                kind="key_relaxed",
                entity=entity_name,
                property=before.name,
                hint="campo saiu da chave: consultas por chave podem alterar o resultado",
            )
        )
    if not before.key and after.key:
        changes.append(
            Change(
                severity=SEVERITY_BREAKING,
                kind="key_added",
                entity=entity_name,
                property=before.name,
                hint="campo entrou na chave: consultas por chave podem alterar o resultado",
            )
        )
    return changes


def diff_contracts(before: Contract | None, after: Contract) -> DriftReport:
    """Compara dois contratos. `before=None` significa primeira observacao."""
    if before is None:
        return DriftReport(
            system_key="",
            status=ObservationStatus.FIRST_OBSERVATION,
            severity=SEVERITY_NONE,
            changes=(),
            fingerprint_after=after.fingerprint(),
            kind=after.kind,
        )
    if before.kind != after.kind:
        # Trocar de tipo de contrato (OData -> RFC) nao e' drift, e' outro
        # sistema apontado no catalogo. Tratar como mudanca de campo
        # produziria centenas de alarme falsos.
        return DriftReport(
            system_key="",
            status=ObservationStatus.DRIFT,
            severity=SEVERITY_BREAKING,
            changes=(
                Change(
                    severity=SEVERITY_BREAKING,
                    kind="contract_kind_changed",
                    entity="*",
                    old=before.kind,
                    new=after.kind,
                    hint="o baseline gravado e' de outro tipo de contrato; reveja o catalogo",
                ),
            ),
            fingerprint_before=before.fingerprint(),
            fingerprint_after=after.fingerprint(),
            kind=after.kind,
        )

    changes: list[Change] = []
    for entity_name in sorted(set(before.entities) | set(after.entities)):
        old_entity = before.entities.get(entity_name)
        new_entity = after.entities.get(entity_name)
        if old_entity is None:
            changes.append(
                Change(
                    severity=SEVERITY_ADDITIVE,
                    kind="entity_added",
                    entity=entity_name,
                    new="presente",
                )
            )
            continue
        if new_entity is None:
            changes.append(
                Change(
                    severity=SEVERITY_BREAKING,
                    kind="entity_removed",
                    entity=entity_name,
                    old="presente",
                )
            )
            continue
        if not old_entity.abstract and new_entity.abstract:
            changes.append(
                Change(
                    severity=SEVERITY_BREAKING,
                    kind="abstract",
                    entity=entity_name,
                    old="concreta",
                    new="abstrata",
                )
            )
        removed = {
            name: prop
            for name, prop in old_entity.properties.items()
            if name not in new_entity.properties
        }
        added = {
            name: prop
            for name, prop in new_entity.properties.items()
            if name not in old_entity.properties
        }
        renames = _pair_renames(removed, added)
        for old_name, new_name in renames.items():
            changes.append(
                Change(
                    severity=SEVERITY_BREAKING,
                    kind="property_removed",
                    entity=entity_name,
                    property=old_name,
                    old=removed[old_name].type_name,
                    new=new_name,
                    hint=f"provavel rename: {old_name} -> {new_name} (mesma assinatura)",
                )
            )
        for old_name in sorted(set(removed) - set(renames)):
            changes.append(
                Change(
                    severity=SEVERITY_BREAKING,
                    kind="property_removed",
                    entity=entity_name,
                    property=old_name,
                    old=removed[old_name].type_name,
                )
            )
        for new_name in sorted(set(added) - set(renames.values())):
            changes.append(
                Change(
                    severity=SEVERITY_ADDITIVE,
                    kind="property_added",
                    entity=entity_name,
                    property=new_name,
                    new=added[new_name].type_name,
                )
            )
        for name in sorted(set(old_entity.properties) & set(new_entity.properties)):
            changes.extend(
                _diff_properties(
                    entity_name, old_entity.properties[name], new_entity.properties[name]
                )
            )

    for param_name in sorted(set(before.parameters) | set(after.parameters)):
        old_param = before.parameters.get(param_name)
        new_param = after.parameters.get(param_name)
        if old_param is None:
            changes.append(
                Change(
                    severity=SEVERITY_ADDITIVE,
                    kind="parameter_added",
                    entity="*",
                    property=param_name,
                    new=new_param.type_name if new_param else None,
                )
            )
        elif new_param is None:
            changes.append(
                Change(
                    severity=SEVERITY_BREAKING,
                    kind="parameter_removed",
                    entity="*",
                    property=param_name,
                    old=old_param.type_name,
                    hint="parametro obrigatorio pode ter virado opcional ou sumido",
                )
            )

    # `cosmetic` nao e emitido: ver docstring do modulo.
    all_changes = changes

    # Determina severidade global: breaking > additive > cosmetic > none
    severity = SEVERITY_NONE
    for change in all_changes:
        if SEVERITY_ORDER[change.severity] > SEVERITY_ORDER[severity]:
            severity = change.severity

    # Se apenas mudancas cosméticas, o status e' DRIFT mas o incidente NAO e' aberto
    # (observe.py::to_incident_data filtra breaking+is_breaking).
    status = ObservationStatus.CLEAN if severity == SEVERITY_NONE else ObservationStatus.DRIFT
    return DriftReport(
        system_key="",
        status=status,
        severity=severity,
        changes=tuple(all_changes),
        fingerprint_before=before.fingerprint(),
        fingerprint_after=after.fingerprint(),
        kind=after.kind,
    )


def unverified_report(system_key: str, reason: str) -> DriftReport:
    """Nao deu para ler o contrato. Deliberadamente NAO e' `CLEAN`.

    O preflight de RAM e' fail-open porque uma medicao faltando nao pode
    impedir o trabalho. Aqui e' o oposto: quem consome este report usa a
    saida para decidir se abre incidente, e um "sem drift" inventado aqui
    vira integracao quebrada nao detectada. Falha de leitura ganha estado
    proprio e visivel.
    """
    return DriftReport(
        system_key=system_key,
        status=ObservationStatus.UNVERIFIED,
        severity=SEVERITY_NONE,
        changes=(),
        reason=reason,
    )
