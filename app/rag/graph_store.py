"""GraphRAG - enriquecimento do diagnostico com historico relacional
(Neo4j), reservado para uso futuro (ver docs/ARCHITECTURE.md).

Desligado por default (`GRAPH_RAG_ENABLED=false`). O motivo de existir
desligado por default: Qdrant (busca semantica em texto) ja resolve o
caso de uso principal (achar o documento de conhecimento certo); o
grafo so agrega valor quando ha HISTORICO acumulado de incidentes reais
(nao os 4-8 documentos de demonstracao deste repositorio) - relacionar
"essa RFC destination ja teve N incidentes antes, com essas causas" so
faz sentido depois de meses de uso real, nao no dia 1.

O que este modulo faz quando ligado:
  1. `upsert_incident_graph(...)` - depois de cada diagnostico, grava
     no Neo4j os nos (Incident, Interface, System, Document) e as
     relacoes entre eles
  2. `graph_context(...)` - antes de gerar o diagnostico, consulta o
     grafo por incidentes ANTERIORES na mesma interface/sistema, para
     dar ao LLM contexto de recorrencia ("isso ja aconteceu 3x, sempre
     pela mesma causa") que a busca vetorial sozinha nao da (Qdrant
     acha o documento de CONHECIMENTO mais parecido, nao o HISTORICO
     relacional de uma interface especifica)

Como ativar de verdade (nao ha nada para descomentar no Python - so
infraestrutura + uma flag):
  1. `docker compose --profile graphrag up -d neo4j`
  2. `GRAPH_RAG_ENABLED=true` no `.env` (+ NEO4J_PASSWORD se voce
     mudou o default do docker-compose)
  3. Rodar `uv run python -m app.rag.graph_store --init` uma vez, para
     criar as constraints/indices

Testado com um driver Neo4j FAKE (`tests/test_graph_store.py`) que
verifica as queries Cypher e o mapeamento de dados - NAO testado
contra um Neo4j real (ver ressalva equivalente em
`RFCConnector._fetch_real`), porque a decisao de negocio foi manter
Neo4j fora do `docker compose up` default (ver Decisao de Arquitetura
#9 no README) - subir e derrubar so para este teste teria custo maior
que o beneficio nesta fase.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

from neo4j.exceptions import DriverError, TransientError

from app.config import settings

# DA-21: excecoes que sinalizam "Neo4j inalcancavel agora" (conexao
# recusada, pool esgotado, servidor temporariamente indisponivel) -
# `DriverError` cobre falhas do LADO DO CLIENTE (nunca chegou a
# conversar com o servidor) e `TransientError` cobre respostas do
# servidor que dizem "tente de novo depois" (ex: `DatabaseUnavailable`
# durante um restart). Deliberadamente NAO inclui `Neo4jError` em
# geral: um `ConstraintError`/`CypherSyntaxError` significa que a
# query ou o schema estao errados - um bug nosso, nao indisponibilidade
# de infra - e deve continuar subindo normalmente, mesmo principio que
# separa falha de transporte de erro de aplicacao no Hybrid Inference
# (DA-20, app/llm/factory.py).
GRAPH_UNAVAILABLE_EXCEPTIONS = (DriverError, TransientError)


class _Neo4jSession(Protocol):
    """Subconjunto do protocolo `neo4j.Session` que este modulo usa -
    permite injetar um driver/sessao fake nos testes sem precisar de
    um Neo4j real (mesmo espirito de `httpx.MockTransport` nos
    conectores HTTP)."""

    def run(self, query: str, **parameters: Any) -> Any: ...


# Abaixo do qual um incidente gravado no grafo e tratado como hipotese
# fraca (nao confirmada) em consultas futuras - mesmo limiar conceitual
# do EVIDENCE_CONFIDENCE_MARGIN em app/agent/nodes.py, aplicado aqui do
# lado da leitura: sem isso, uma causa raiz de baixa confianca gravada
# hoje volta como "historico" (fato) para o proximo incidente na mesma
# interface - o LLM nao tem como saber que era so uma hipotese (DA-16).
GROUNDED_EVIDENCE_THRESHOLD = 0.5

# A5: limiar elevado para gravar is_grounded=True quando existe historico
# VERIFICADO na mesma interface que DIVERGE da nova hipotese. Sem essa
# protecao, um LLM com evidence_strength razoavel (>=0.5) mas que
# contradiz o que humanos/sistemas ja confirmaram pode contaminar o grafo
# com uma hipotese nao corroborada que volta como "verdade historica".
# Dois limiares distintos:
#   - GROUNDED_EVIDENCE_THRESHOLD (0.5):  caminho sem historico verificado
#   - GROUNDED_CROSS_VALIDATION_THRESHOLD (0.75): caminho com divergencia
GROUNDED_CROSS_VALIDATION_THRESHOLD = 0.75


@dataclass
class RelatedIncident:
    interface_identifier: str
    source_system: str
    root_cause: str
    matched_document: str | None
    evidence_strength: float = 0.0
    is_grounded: bool = False
    # DA-28 (VERIFIED_AS): distinto de is_grounded (proxy AUTOMATICO
    # baseado em threshold de evidence_strength, DA-16) - verified so
    # fica True apos uma chamada EXPLICITA a verify_incident() (humana
    # ou de outro sistema), nunca por inferencia de score. Quando
    # verified=True, verified_root_cause e a causa raiz CONFIRMADA
    # (pode divergir de root_cause, a hipotese original do LLM).
    verified: bool = False
    verified_root_cause: str | None = None


def is_enabled() -> bool:
    return settings.graph_rag_enabled


@lru_cache(maxsize=1)
def _get_driver():
    """Import tardio de `neo4j` - so acontece se GraphRAG estiver
    habilitado, para nao exigir um Neo4j vivo em nenhum caminho
    default (mock/demo) do projeto."""
    from neo4j import GraphDatabase

    return GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )


def _get_session(session: _Neo4jSession | None = None) -> _Neo4jSession:
    if session is not None:
        return session
    return _get_driver().session()


_CONSTRAINTS = [
    "CREATE CONSTRAINT incident_id IF NOT EXISTS FOR (i:Incident) REQUIRE i.id IS UNIQUE",
    (
        "CREATE CONSTRAINT interface_id IF NOT EXISTS "
        "FOR (f:Interface) REQUIRE (f.type, f.identifier) IS UNIQUE"
    ),
    "CREATE CONSTRAINT system_name IF NOT EXISTS FOR (s:System) REQUIRE s.name IS UNIQUE",
]


def ensure_constraints(session: _Neo4jSession | None = None) -> None:
    sess = _get_session(session)
    for statement in _CONSTRAINTS:
        sess.run(statement)


# A5: consulta para ler as causas raiz VERIFICADAS (via verify_incident)
# existentes na mesma interface, usada por _cross_validate_grounded.
# Limita a 5 registros mais recentes: o suficiente para detectar
# divergencia sem adicionar latencia significativa ao caminho de escrita.
_VERIFIED_HISTORY_QUERY = """
MATCH (f:Interface {type: $interface_type, identifier: $identifier})<-[:AFFECTS]-(i:Incident)
MATCH (i)-[:VERIFIED_AS]->(rc:RootCause)
RETURN rc.text AS verified_root_cause
ORDER BY i.created_at DESC
LIMIT 5
"""


def _cross_validate_grounded(
    root_cause: str,
    interface_type: str,
    identifier: str,
    sess: _Neo4jSession,
) -> bool:
    """A5: retorna False se existe historico VERIFICADO nesta interface
    e a nova hipotese diverge de TODAS as causas raiz confirmadas.

    Logica conservadora (falha aberta = mais permissiva):
    - Sem historico verificado -> True  (nada para comparar, threshold normal)
    - Com historico, ALGUMA causa confirmada bate com a hipotese -> True
    - Com historico, NENHUMA causa confirmada bate -> False (threshold elevado)

    "Bate" usa heuristica de substring bidirecional: verificacoes humanas
    geralmente usam vocabulario similar ao da hipotese original (ex:
    "RFC_DEST invalido" bate com "RFC Destination invalido"), sem precisar
    de embeddings extras que adicionariam latencia ao caminho de escrita.
    Exige ao menos 4 caracteres para evitar matches espurios com siglas
    curtas ("SAP", "RFC", etc.).

    Por que nao cosine similarity aqui: este e o caminho de ESCRITA do
    grafo, executado apos cada diagnostico. Embeddings exigiriam chamar
    o sentence-transformer (CPU-bound ~50ms) ou o LLM (latencia de rede).
    A heuristica O(n*m) com n<=5 e len<=500 e desprezivel. Revisao futura
    pode substituir se a base de incidentes verificados crescer o suficiente
    para gerar falsos negativos relevantes.
    """
    result = sess.run(
        _VERIFIED_HISTORY_QUERY,
        interface_type=interface_type,
        identifier=identifier,
    )
    verified_causes = [
        record["verified_root_cause"]
        for record in result
        if record["verified_root_cause"]
    ]
    if not verified_causes:
        # Sem historico verificado: usa threshold normal, nada para divergir
        return True

    root_cause_lower = root_cause.lower()
    for vc in verified_causes:
        vc_lower = vc.lower()
        if len(vc_lower) >= 4 and vc_lower in root_cause_lower:
            return True
        if len(root_cause_lower) >= 4 and root_cause_lower in vc_lower:
            return True
    # Nenhuma causa verificada compativel: hipotese diverge do historico
    return False


_UPSERT_QUERY = """
MERGE (i:Incident {id: $incident_id})
  SET i.description = $description, i.root_cause = $root_cause,
      i.confidence = $confidence, i.evidence_strength = $evidence_strength,
      i.is_grounded = $is_grounded, i.created_at = datetime()
MERGE (f:Interface {type: $interface_type, identifier: $identifier})
MERGE (i)-[:AFFECTS]->(f)
MERGE (s:System {name: $source_system})
MERGE (f)-[:RUNS_ON]->(s)
WITH i
FOREACH (_ IN CASE WHEN $matched_document IS NOT NULL THEN [1] ELSE [] END |
  MERGE (d:Document {source: $matched_document})
  MERGE (i)-[:HAS_ROOT_CAUSE_IN]->(d)
)
"""


def upsert_incident_graph(
    incident_id: str,
    description: str,
    interface_type: str | None,
    identifier: str | None,
    source_system: str | None,
    root_cause: str,
    confidence: float,
    matched_document: str | None,
    evidence_strength: float = 0.0,
    session: _Neo4jSession | None = None,
) -> None:
    """Grava um diagnostico concluido no grafo. No-op silencioso se
    nao houver interface/identificador (incidente so-texto, sem
    conector) - nao ha o que relacionar nesse caso.

    evidence_strength (DA-16): sinal objetivo (nao a confidence
    auto-relatada pelo LLM) de quao fundamentado era o diagnostico no
    momento em que foi gravado - ver
    app.agent.nodes._compute_evidence_strength. Persistido junto com o
    incidente para que consultas futuras (graph_context) possam
    distinguir "fato observado" de "hipotese nao confirmada" em vez de
    tratar toda gravacao anterior como historico validado."""
    if not interface_type or not identifier:
        return
    sess = _get_session(session)

    # A5: determinacao de is_grounded com cross-validation anti-poisoning.
    # Passo 1 - threshold base: se a hipotese nao atinge GROUNDED_EVIDENCE_THRESHOLD
    #   ela sera False sem precisar consultar o historico.
    # Passo 2 - cross-validation: so executada quando a hipotese atingiria
    #   is_grounded=True pelo threshold base. Verifica se existe historico VERIFICADO
    #   que diverge da nova hipotese; se diverge, exige GROUNDED_CROSS_VALIDATION_THRESHOLD
    #   (0.75) em vez de GROUNDED_EVIDENCE_THRESHOLD (0.5).
    # Resultado: um LLM fabricando uma causa raiz com evidence_strength entre 0.5 e 0.75
    #   que contradiz o que humanos ja confirmaram NAO contamina o historico do grafo.
    base_grounded = evidence_strength >= GROUNDED_EVIDENCE_THRESHOLD
    if base_grounded:
        cross_validates = _cross_validate_grounded(
            root_cause=root_cause,
            interface_type=interface_type,
            identifier=identifier,
            sess=sess,
        )
        if not cross_validates:
            # Hipotese diverge do historico verificado: exige threshold elevado
            base_grounded = evidence_strength >= GROUNDED_CROSS_VALIDATION_THRESHOLD

    sess.run(
        _UPSERT_QUERY,
        incident_id=incident_id,
        description=description,
        interface_type=interface_type,
        identifier=identifier,
        source_system=source_system or interface_type,
        root_cause=root_cause,
        confidence=confidence,
        matched_document=matched_document,
        evidence_strength=evidence_strength,
        is_grounded=base_grounded,
    )


_VERIFY_QUERY = """
MATCH (i:Incident {id: $incident_id})
MERGE (rc:RootCause {text: $verified_root_cause})
MERGE (i)-[v:VERIFIED_AS]->(rc)
  SET v.verified_by = $verified_by, v.verified_at = datetime()
SET i.verified = true
RETURN i.id AS incident_id
"""


def verify_incident(
    incident_id: str,
    verified_root_cause: str,
    verified_by: str = "human",
    session: _Neo4jSession | None = None,
) -> bool:
    """DA-28 (VERIFIED_AS): registra uma verificacao EXPLICITA (humana
    ou de outro sistema) da causa raiz de um incidente ja gravado no
    grafo - o unico caminho que marca `verified=true` num Incident.

    Isso e deliberadamente distinto de `is_grounded` (DA-16): grounded
    e um proxy AUTOMATICO baseado em `evidence_strength >=
    GROUNDED_EVIDENCE_THRESHOLD` no momento do diagnostico - ainda e a
    hipotese do LLM, so que com evidencia forte o suficiente para nao
    ser descartada de cara. `verified` e um fato POSTERIOR, aplicado
    por alguem (ou algum sistema downstream, ex: ticket fechado como
    "causa confirmada: X") que efetivamente confirmou o que aconteceu
    - inclusive quando isso diverge da hipotese original do LLM
    (`verified_root_cause` pode ser diferente de `root_cause`). Sem
    essa distincao explicita, uma hipotese razoavelmente confiante do
    LLM (grounded=true) acabaria indistinguivel de um fato confirmado
    por um humano depois de investigar - o "loop de retroalimentacao
    epistemico" que a revisao externa apontou como o maior risco do
    GraphRAG: hipoteses do LLM viram "verdade historica" via
    round-trips pelo grafo, sem nunca terem sido de fato confirmadas.

    Modelagem: `(Incident)-[:VERIFIED_AS {verified_by, verified_at}]->
    (RootCause {text})` em vez de so uma propriedade plana no
    Incident - um no `RootCause` separado permite (no futuro) agregar
    quantos incidentes distintos compartilham a mesma causa raiz
    confirmada, sem reprocessar texto livre.

    Retorna False (no-op) se GraphRAG estiver desligado ou se
    `incident_id` nao existir no grafo (nada foi verificado)."""
    if not is_enabled():
        return False
    sess = _get_session(session)
    result = sess.run(
        _VERIFY_QUERY,
        incident_id=incident_id,
        verified_root_cause=verified_root_cause,
        verified_by=verified_by,
    )
    record = next(iter(result), None)
    return record is not None


_RELATED_QUERY = """
MATCH (f:Interface {type: $interface_type, identifier: $identifier})<-[:AFFECTS]-(i:Incident)
OPTIONAL MATCH (i)-[:HAS_ROOT_CAUSE_IN]->(d:Document)
OPTIONAL MATCH (f)-[:RUNS_ON]->(s:System)
OPTIONAL MATCH (i)-[:VERIFIED_AS]->(rc:RootCause)
RETURN i.root_cause AS root_cause, s.name AS source_system, d.source AS matched_document,
       coalesce(i.evidence_strength, 0.0) AS evidence_strength,
       coalesce(i.is_grounded, false) AS is_grounded,
       coalesce(i.verified, false) AS verified,
       rc.text AS verified_root_cause
ORDER BY i.created_at DESC
LIMIT $limit
"""


def graph_context(
    interface_type: str | None,
    identifier: str | None,
    limit: int = 5,
    include_ungrounded: bool = False,
    session: _Neo4jSession | None = None,
) -> list[RelatedIncident]:
    """Retorna incidentes anteriores conhecidos na mesma interface,
    mais recentes primeiro. Lista vazia se GraphRAG estiver desligado,
    sem historico, ou sem interface/identificador informado - sempre
    seguro de chamar incondicionalmente do grafo LangGraph.

    include_ungrounded (DA-16): por padrao False - incidentes gravados
    com evidence_strength abaixo de GROUNDED_EVIDENCE_THRESHOLD (ou sem
    o campo, gravados antes desta mudanca) sao hipoteses nao
    confirmadas do LLM, nao fatos observados, e ficam de fora do
    contexto injetado no prompt de novos diagnosticos para nao virarem
    "verdade historica" por repeticao. Passar True so em ferramentas de
    auditoria/analise, nunca no caminho de producao dos sub-agentes de
    diagnostico (sap_diagnosis_node/saas_diagnosis_node, DA-22).

    DA-28: um incidente `verified=true` (via `verify_incident()`)
    SEMPRE passa neste filtro, mesmo com `is_grounded=false` - uma
    verificacao humana/sistema explicita e evidencia mais forte que o
    proxy automatico de `evidence_strength`, entao nunca deveria ser
    escondida do prompt so porque o diagnostico ORIGINAL teve baixa
    confianca. O inverso nao existe: `is_grounded=true` continua
    entrando mesmo sem verificacao, como antes desta mudanca."""
    if not is_enabled() or not interface_type or not identifier:
        return []

    sess = _get_session(session)
    result = sess.run(
        _RELATED_QUERY, interface_type=interface_type, identifier=identifier, limit=limit
    )
    related = [
        RelatedIncident(
            interface_identifier=identifier,
            source_system=record["source_system"] or interface_type,
            root_cause=record["root_cause"] or "",
            matched_document=record["matched_document"],
            evidence_strength=float(record["evidence_strength"]),
            is_grounded=bool(record["is_grounded"]),
            verified=bool(record["verified"]),
            verified_root_cause=record["verified_root_cause"],
        )
        for record in result
    ]
    if include_ungrounded:
        return related
    return [r for r in related if r.is_grounded or r.verified]


def format_graph_context_for_prompt(related: list[RelatedIncident]) -> str:
    """Formata o historico relacional como bloco de texto pronto para
    entrar no prompt de diagnostico - separado da consulta em si para
    poder ser testado sem depender de nenhum driver.

    DA-21: agrupa ocorrencias CONSECUTIVAS da mesma causa raiz (mesmo
    root_cause + matched_document + is_grounded) em uma linha com
    contador ("ja ocorreu 3x") em vez de repetir a mesma linha - uma
    interface "flapping" (falhando repetidamente pela mesma causa)
    esgotava o orcamento de contexto do prompt com linhas identicas em
    vez de sinalizar recorrencia, que e justamente o dado mais util
    que uma camada de conhecimento OPERACIONAL deveria destacar.
    Agrupamento e so entre vizinhos (a lista ja vem mais-recente-
    primeiro) - nao junta ocorrencias intercaladas com causas
    diferentes, para nao esconder que outra coisa aconteceu no meio.

    DA-28: tres niveis de confianca agora, do mais forte ao mais fraco
    - "VERIFICADA" (`verified=true`, confirmada explicitamente por
    humano/sistema via `verify_incident()`), "confirmada anteriormente"
    (`is_grounded=true`, proxy automatico de `evidence_strength`, DA-16)
    e "HIPOTESE NAO CONFIRMADA" (nenhum dos dois - so a hipotese
    original do LLM, baixa evidencia). Quando verificada, a linha usa
    `verified_root_cause` (a causa CONFIRMADA, que pode divergir da
    hipotese original `root_cause`) em vez de `root_cause` - o LLM nao
    deve ver as duas misturadas sem saber qual e o fato."""
    if not related:
        return ""

    grouped: list[tuple[RelatedIncident, int]] = []
    for r in related:
        if grouped:
            prev, count = grouped[-1]
            if (
                prev.root_cause == r.root_cause
                and prev.matched_document == r.matched_document
                and prev.is_grounded == r.is_grounded
                and prev.verified == r.verified
                and prev.verified_root_cause == r.verified_root_cause
            ):
                grouped[-1] = (prev, count + 1)
                continue
        grouped.append((r, 1))

    lines = []
    for r, count in grouped:
        if r.verified:
            label = "causa raiz VERIFICADA (confirmada por humano/sistema)"
            root_cause_text = r.verified_root_cause or r.root_cause
        elif r.is_grounded:
            label = "causa raiz confirmada anteriormente"
            root_cause_text = r.root_cause
        else:
            label = "HIPOTESE NAO CONFIRMADA de diagnostico anterior (baixa evidencia - nao trate como fato)"
            root_cause_text = r.root_cause
        occurrence = f" (ja ocorreu {count}x)" if count > 1 else ""
        lines.append(
            f"- {label}{occurrence}: {root_cause_text} (documento: {r.matched_document or 'N/A'})"
        )

    return (
        f"\nHistorico conhecido desta interface ({len(related)} incidente(s) anterior(es), "
        f"mais recente primeiro):\n" + "\n".join(lines) + "\n"
    )


_PRUNE_UNGROUNDED_QUERY = """
MATCH (i:Incident)
WHERE coalesce(i.is_grounded, false) = false
  AND i.created_at < datetime() - duration({days: $older_than_days})
DETACH DELETE i
RETURN count(i) AS deleted_count
"""


def prune_ungrounded_hypotheses(
    older_than_days: int = 90, session: _Neo4jSession | None = None
) -> int:
    """Remove hipoteses NAO confirmadas (`is_grounded=false`) gravadas
    ha mais de `older_than_days` dias - manutencao MANUAL, nunca
    automatica (nao e chamada de nenhum node do grafo nem do
    lifespan). Incidentes `is_grounded=true` (causa raiz confirmada)
    NUNCA sao tocados por esta funcao, sob nenhuma idade - so a
    hipotese fraca que nunca foi corroborada e que so ocupa espaco e
    pode confundir uma leitura manual do grafo. Mesmo principio de
    "nunca deletar sem o operador pedir explicitamente" usado em outras
    partes deste projeto, aplicado aqui no nivel de dado do grafo em
    vez de arquivo. Retorna quantos incidentes foram removidos."""
    sess = _get_session(session)
    result = sess.run(_PRUNE_UNGROUNDED_QUERY, older_than_days=older_than_days)
    record = next(iter(result), None)
    return int(record["deleted_count"]) if record else 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Utilitarios de manutencao do GraphRAG (Neo4j)")
    parser.add_argument(
        "--init", action="store_true", help="Cria as constraints/indices necessarias"
    )
    parser.add_argument(
        "--prune-ungrounded",
        action="store_true",
        help=(
            "Remove hipoteses NAO confirmadas (is_grounded=false) gravadas ha mais de "
            "--older-than-days dias. Incidentes confirmados nunca sao afetados."
        ),
    )
    parser.add_argument(
        "--older-than-days",
        type=int,
        default=90,
        help="Usado com --prune-ungrounded (default: 90 dias)",
    )
    args = parser.parse_args()

    if not is_enabled():
        print(
            "GRAPH_RAG_ENABLED=false - nada a fazer. Ative no .env e suba o "
            "Neo4j (docker compose --profile graphrag up -d neo4j) primeiro."
        )
    elif args.init:
        ensure_constraints()
        print("Constraints/indices do GraphRAG criados/confirmados no Neo4j.")
    elif args.prune_ungrounded:
        deleted = prune_ungrounded_hypotheses(older_than_days=args.older_than_days)
        print(
            f"{deleted} hipotese(s) nao confirmada(s) com mais de "
            f"{args.older_than_days} dia(s) removida(s). Incidentes confirmados "
            "nao foram afetados."
        )
    else:
        parser.print_help()
