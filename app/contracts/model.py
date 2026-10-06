"""DA-52: contrato normalizado de um sistema integrado.

O problema que a DA-52 existe para resolver e' concreto e antigo: o conector
OData le `record.get("Status", "UNKNOWN")` e o RFC le `result.get("STATUS")`.
O nome do campo esta escrito no codigo. Se o SAP renomear, remover ou
mudar o tipo desse campo, o conector continua devolvendo HTTP 200 e um
`UNKNOWN` silencioso, e o rule engine (DA-33) nao ve nada -- porque o
sintoma nao e' um codigo de erro, e' um campo que sumiu. Integracao que
"funciona" carregando dado errado e' o pior modo de falha, e' o unico que
ninguem nota.

A solucao: o proprio sistema publica o contrato dele. OData v4 expoe
`$metadata` (EDMX) de graca e autoritativo; RFC expoe a descricao da
function module; IDoc, os segmentos. Este modulo guarda o contrato em uma
forma NORMALIZADA, sem o que muda a cada publicacao, para que o
comparativo entre ontem e hoje signifique alguma coisa.

Duas regras que sustentam o resto:

1. Normalizar ANTES de hashear. O `$metadata` muda byte a byte a cada
   publish no SAP Gateway: espacos, ordem dos elementos, e a anotacao
   `OData.Community.V1.VocabularyMetadata` (com `Version`) que sobe de
   versao a cada save. Hashear o XML bruto daria drift permanente e falso.
   Mesmo contrato, mesma impressao digital -- e' a DA-2 (determinismo)
   aplicada a um dominio novo.

2. O que e' volatil fica de fora, e fica ESCRITO onde fica. Namespace,
   anotacoes e version sao ignorados de proposito; daqui a seis meses
   alguem vai querer "melhorar" e readicionar. Este arquivo e' o lugar de
   dizer nao e de dizer por que.

Nada aqui chama LLM: o contrato e' lido do sistema e a classificacao e'
deterministica (mesmo principio da DA-3/DA-16, onde o guardrail mora no
codigo e nao no prompt).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field

# Vocabulario de origem do contrato. `kind` entra na impressao digital:
# um IDoc e' um IDoc, nao um OData, mesmo que os nomes coincidam.
KIND_ODATA = "odata_v4"
KIND_RFC_FM = "rfc_function_module"
KIND_IDOC = "idoc"

CONTRACT_KINDS = frozenset({KIND_ODATA, KIND_RFC_FM, KIND_IDOC})


@dataclass(frozen=True)
class Property:
    """Um campo do contrato, ja normalizado.

    `nullable` e' o valor ABSOLUTO observado, nao um delta: e' assim que o
    contrato editado se compara com o anterior sem ambiguidade. Ausencia de
    atributo no XML significa nullable=true (padrao EDMX), e `None` e' reservado
    para "o contrato nao diz" -- que no $metadata nunca acontece, mas num
    descricao de RFC mais pobre pode.
    """

    name: str
    type_name: str
    nullable: bool = True
    max_length: int | None = None
    key: bool = False
    navigation: bool = False

    def as_dict(self) -> dict[str, object]:
        """Forma canonica. Determinismo exige que o dicionario EXATO que
        vai para o hash seja o mesmo toda vez -- chave nova entra aqui, ou
        o hash muda sem que o contrato mude."""
        return {
            "type": self.type_name,
            "nullable": self.nullable,
            "max_length": self.max_length,
            "key": self.key,
            "navigation": self.navigation,
        }

    def signature(self) -> tuple[str, bool, int | None, bool, bool]:
        return (
            self.type_name,
            self.nullable,
            self.max_length,
            self.key,
            self.navigation,
        )


@dataclass(frozen=True)
class Entity:
    name: str
    properties: Mapping[str, Property] = field(default_factory=dict)
    abstract: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "abstract": self.abstract,
            # sorted() e' o que impede "ordem de declaracao" de virar
            # fingerprint: o XML pode reordenar propriedades sem que o
            # contrato tenha mudado.
            "properties": {name: prop.as_dict() for name, prop in sorted(self.properties.items())},
        }


@dataclass(frozen=True)
class Contract:
    """O contrato inteiro, normalizado e comparavel."""

    kind: str
    entities: Mapping[str, Entity] = field(default_factory=dict)
    parameters: Mapping[str, Property] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in CONTRACT_KINDS:
            raise ValueError(f"kind de contrato desconhecido: {self.kind!r}")

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "entities": {name: entity.as_dict() for name, entity in sorted(self.entities.items())},
            "parameters": {name: prop.as_dict() for name, prop in sorted(self.parameters.items())},
        }

    def canonical_json(self) -> str:
        """Serializacao estavel: sort_keys + sem espacos. Dois contratos
        semanticamente iguais produzem bytes identicos -- e' o que permite
        decidir "houve mudanca?" por hash, sem varrer estrutura em memoria
        a cada observacao."""
        return json.dumps(
            self.as_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def property_count(self) -> int:
        return sum(len(entity.properties) for entity in self.entities.values()) + len(
            self.parameters
        )

    def entity_names(self) -> list[str]:
        return sorted(self.entities)
