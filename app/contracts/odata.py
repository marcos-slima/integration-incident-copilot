"""DA-52: leitura do contrato OData v4 a partir do `$metadata` (EDMX).

Usa so `xml.etree.ElementTree`: o EDMX e' XML simples e acrescentar um
parser CSDL como dependencia para o ganho seria trocar risco de supply
chain por conveniencia.

Duas defesas de armadilhas reais de SAP Gateway, ambas documentadas em
`IGNORED`:

- Anotacoes. `OData.Community.V1.VocabularyMetadata` carrega `Version` e
  muda a cada publish, mesmo sem mudanca de campo. Comparar isso produz
  drift falso em TODA publicacao -- exatamente o tipo de alarme que faz
  um time desativar o detector na segunda semana.
- Namespace. O SAP gera namespace novo quando o servico e' republicado.
  Ele muda a URL mas nao muda a compatibilidade de nenhum consumidor.
  Fica de fora, e o motivo fica escrito.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Mapping

from defusedxml import ElementTree as defused_element_tree
from defusedxml.common import DefusedXmlException

from app.contracts.model import KIND_ODATA, Contract, Entity, Property

#: Tag que carrega a chave de uma entity type no EDMX 4.0. Em EDMX legado
#: (CSDL 3.0) a chave vem de <Key><PropertyRef Name=.../></Key>.
_KEY_TERM = "org.odata.core.v1.key"


class MetadataError(ValueError):
    """`$metadata` ilegivel ou sem entity type. Nao e' drift: e' falta de
    dado, e o chamador tem de transformar isso em `unverified`, nunca em
    "sem drift"."""


def _local(tag: str) -> str:
    """Nome local da tag. O EDMX aparece sob varias URIs de namespace
    (edm, edmx, CSDL 4.0) e casar pela string inteira quebraria a cada
    mudanca de namespace no proprio SAP -- que e' volatil por definicao."""
    return tag.rsplit("}", 1)[-1]


def _bool_attr(element: ET.Element, name: str, default: bool = True) -> bool:
    raw = element.get(name)
    if raw is None:
        return default
    return raw.strip().lower() == "true"


def _int_attr(element: ET.Element, name: str) -> int | None:
    raw = element.get(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        # MaxLength="max"/"Max" e' valido no EDMX para string sem limite e
        # nao e' um inteiro. Tratar como ausente mantem o contrato
        # comparavel; virar 0 faria o diff acusar mudanca de limite.
        return None


def _is_key(element: ET.Element) -> bool:
    for child in element:
        if _local(child.tag) != "Annotation":
            continue
        term = (child.get("Term") or "").lower()
        if term == _KEY_TERM or term.endswith(".key"):
            return True
    return False


def _parse_property(element: ET.Element) -> Property:
    raw_type = element.get("Type") or ""
    navigation = _local(element.tag) == "NavigationProperty"
    # So o alvo de NavigationProperty tem o namespace retirado: ali o
    # prefixo (`Self.`) e' a MESMA schema local, e namespace e' volatil por
    # decisao (SAP republica com namespace novo). Para tipo primitivo o
    # namespace FAZ PARTE da identidade -- reduzir `Edm.Decimal` a `Decimal`
    # faria `Edm.Decimal` e `Outro.Decimal` casarem, que e' um falso "sem
    # mudanca" dentro do detector cuja unica funcao e' nao perder mudanca.
    type_name = raw_type.rsplit(".", 1)[-1] if (raw_type and navigation) else raw_type
    return Property(
        name=element.get("Name") or "",
        type_name=type_name,
        # CSDL v2/v4: absence of Nullable means nullable=true. Omitting the
        # attribute is the common case, and defaulting to False inverted the
        # semantics (DATA-01).
        nullable=_bool_attr(element, "Nullable", default=True),
        max_length=_int_attr(element, "MaxLength"),
        key=_is_key(element),
        navigation=navigation,
    )


def _parse_entity_type(element: ET.Element) -> Entity:
    properties: dict[str, Property] = {}
    keys: set[str] = set()
    for child in element:
        tag = _local(child.tag)
        if tag in {"Property", "NavigationProperty"}:
            prop = _parse_property(child)
            if prop.name:
                properties[prop.name] = prop
        elif tag == "Key":  # CSDL legado
            for ref in child:
                if _local(ref.tag) == "PropertyRef" and ref.get("Name"):
                    keys.add(ref.get("Name") or "")
    if keys:
        properties = {
            name: (
                Property(
                    name=prop.name,
                    type_name=prop.type_name,
                    nullable=prop.nullable,
                    max_length=prop.max_length,
                    key=True,
                    navigation=prop.navigation,
                )
                if name in keys
                else prop
            )
            for name, prop in properties.items()
        }
    return Entity(
        name=element.get("Name") or "",
        properties=properties,
        abstract=_bool_attr(element, "Abstract", default=False),
    )


def _parse_container(element: ET.Element, entities: dict[str, Entity]) -> None:
    """EntityContainer -> EntitySet -> EntityType, para o caso de o
    EntityType morar num Schema diferente do container. Em CSDL 4.0 tudo
    esta junto, mas o desenho do XML nao e' garantido."""
    wanted: dict[str, str] = {}
    for entity_set in element.iter():
        if _local(entity_set.tag) != "EntitySet":
            continue
        name = entity_set.get("Name")
        entity_type = entity_set.get("EntityType")
        if name and entity_type:
            wanted[entity_type.rsplit(".", 1)[-1]] = name
    for entity_type_name, entity_set_name in wanted.items():
        if entity_type_name in entities:
            continue
        # EntitySet cujo EntityType nao aparece: o contrato esta incompleto
        # de um jeito que o consumidor nao consegue prever. Registrado com o
        # nome do set para o diff poder dizer "o sumiu", em vez de o parser
        # simplesmente esquecer a entity.
        entities[entity_set_name] = Entity(name=entity_set_name, properties={})


def parse_odata_metadata(xml_text: str) -> Contract:
    """EDMX -> Contract. Levanta `MetadataError` quando nao da para ler.

    Deliberadamente NAO devolve contrato parcial em silencio: contrato meio
    lido pareceria "o campo foi removido", que e' a claimed mais destrutiva
    que este detector pode fazer.
    """
    try:
        root = defused_element_tree.fromstring(xml_text)
    except ET.ParseError as exc:
        raise MetadataError(f"$metadata nao e XML valido: {exc}") from exc
    except DefusedXmlException as exc:
        # DOCTYPE/DTD/entidades no $metadata. O texto vem de um host remoto
        # configurado, entao e' entrada nao confiavel de qualquer jeito; aqui
        # ele vira MetadataError (falhado limpo, que a DA-52 ja exige) em vez
        # de estourar uma excecao de biblioteca pelo grafo.
        raise MetadataError(f"$metadata com XML nao permitido (DTD/entidade): {exc}") from exc

    if _local(root.tag) not in {"Schema", "edmx", "Edmx"}:
        raise MetadataError(f"raiz inesperada em $metadata: {root.tag}")

    entities: dict[str, Entity] = {}
    for element in root.iter():
        if _local(element.tag) != "EntityType":
            continue
        entity = _parse_entity_type(element)
        if entity.name:
            entities[entity.name] = entity

    if not entities:
        raise MetadataError("$metadata sem EntityType: nada a comparar")

    for element in root.iter():
        if _local(element.tag) == "EntityContainer":
            _parse_container(element, entities)

    return Contract(kind=KIND_ODATA, entities=entities)


def contract_from_dict(payload: Mapping[str, object]) -> Contract:
    """Reconstroi um Contract da forma canonica (persistida em JSON)."""
    entities: dict[str, Entity] = {}
    for name, raw in (payload.get("entities") or {}).items():
        raw_map: Mapping[str, object] = raw if isinstance(raw, Mapping) else {}
        raw_props: Mapping[str, object] = (
            raw_map.get("properties") if isinstance(raw_map.get("properties"), Mapping) else {}
        )
        properties = {
            prop_name: Property(
                name=prop_name,
                type_name=str(prop.get("type", "")),
                nullable=bool(prop.get("nullable", False)),
                max_length=prop.get("max_length"),  # type: ignore[arg-type]
                key=bool(prop.get("key", False)),
                navigation=bool(prop.get("navigation", False)),
            )
            for prop_name, prop in raw_props.items()
            if isinstance(prop, Mapping)
        }
        entities[str(name)] = Entity(
            name=str(name), properties=properties, abstract=bool(raw_map.get("abstract", False))
        )

    raw_params: Mapping[str, object] = (
        payload.get("parameters") if isinstance(payload.get("parameters"), Mapping) else {}
    )
    parameters = {
        name: Property(
            name=name,
            type_name=str(prop.get("type", "")) if isinstance(prop, Mapping) else "",
            nullable=bool(prop.get("nullable", True)) if isinstance(prop, Mapping) else True,
        )
        for name, prop in raw_params.items()
    }

    return Contract(
        kind=str(payload.get("kind", KIND_ODATA)), entities=entities, parameters=parameters
    )
