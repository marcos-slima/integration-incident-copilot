"""Gera o diagrama Mermaid do grafo LangGraph a partir do proprio codigo.

Por que gerar: os docs/UC_* removidos no Bloco 5 desenhavam um grafo que
nao existia (`rules_node`, `llm_node`). Aqui a topologia (nos, arestas,
quais arestas sao condicionais) vem de `build_graph().get_graph()`, nas
duas formas que o grafo pode ter (GRAPH_RAG_ENABLED desligado e ligado).
So o TEXTO de cada no e o rotulo de cada aresta condicional sao escritos
a mao, e `tests/test_diagramas.py` reprova se um no novo aparecer sem
descricao ou se o rotulo nao bater com `_route_to_specialist`.

Uso:
    uv run python scripts/graph_diagram.py            # imprime
    uv run python scripts/graph_diagram.py --check    # exit 1 se o doc divergir
    uv run python scripts/graph_diagram.py --write    # regrava o bloco no doc
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "ARCHITECTURE.md"
# Os READMEs mostram so a forma default; o ARCHITECTURE mostra as duas.
README = ROOT / "README.md"  # ingles
README_PT = ROOT / "README.pt-BR.md"
MARCA_INICIO = "<!-- grafo-gerado:inicio (scripts/graph_diagram.py --write; nao editar a mao) -->"
MARCA_FIM = "<!-- grafo-gerado:fim -->"

# Texto de cada no. Chave = nome do no no StateGraph.
DESCRICAO = {
    "__start__": "run_diagnosis",
    "supervisor": "supervisor<br/>classify_domain: sap / saas / generic<br/>(deterministico, sem LLM)",
    "connector": "connector<br/>get_connector(interface_type).fetch(identifier)<br/>real ou cenario demo",
    "retrieve": "retrieve<br/>RAG hibrido + reranker<br/>(fallback reference_library)",
    "ontology_enrich": "ontology_enrich<br/>SKOS/rdflib: upper categories + next steps<br/>(independente de GraphRAG)",
    "hitl_review": "hitl_review<br/>pause para revisao humana se confianca < 0.7<br/>(DA-61 Phase 6)",
    "risk_assessment": "risk_assessment<br/>avalia risk/confidence antes do report<br/>(HITL feedback + ontology candidates)",
    "graph_enrich": "graph_enrich<br/>historico Neo4j<br/>(so verificado vira fato)",
    "sap_diagnose": "sap_diagnose<br/>rule engine; senao LLM via gateway<br/>+ guardrails + evidencia",
    "saas_diagnose": "saas_diagnose<br/>rule engine; senao LLM via gateway<br/>+ guardrails + evidencia",
    "generic_diagnose": "generic_diagnose<br/>rule engine; senao LLM via gateway<br/>+ guardrails + evidencia",
    "graph_write": "graph_write<br/>grava hipotese no Neo4j<br/>(descricao redigida)",
    "report": "report<br/>relatorio Markdown",
    "__end__": "DiagnosisResponse<br/>+ escalation (DA-44)<br/>+ record_incident",
}

DESCRICAO_EN = {
    "__start__": "run_diagnosis",
    "supervisor": "supervisor<br/>classify_domain: sap / saas / generic<br/>(deterministic, no LLM)",
    "connector": "connector<br/>get_connector(interface_type).fetch(identifier)<br/>real system or demo scenario",
    "retrieve": "retrieve<br/>hybrid RAG + reranker<br/>(reference_library fallback)",
    "ontology_enrich": "ontology_enrich<br/>SKOS/rdflib: upper categories + next steps<br/>(independent from GraphRAG)",
    "hitl_review": "hitl_review<br/>pause for human review if confidence < 0.7<br/>(DA-61 Phase 6)",
    "risk_assessment": "risk_assessment<br/>evaluate risk/confidence before report<br/>(HITL feedback + ontology candidates)",
    "graph_enrich": "graph_enrich<br/>Neo4j history<br/>(only human-verified is fact)",
    "sap_diagnose": "sap_diagnose<br/>rule engine; else LLM via gateway<br/>+ guardrails + evidence",
    "saas_diagnose": "saas_diagnose<br/>rule engine; else LLM via gateway<br/>+ guardrails + evidence",
    "generic_diagnose": "generic_diagnose<br/>rule engine; else LLM via gateway<br/>+ guardrails + evidence",
    "graph_write": "graph_write<br/>stores hypothesis in Neo4j<br/>(redacted description)",
    "report": "report<br/>Markdown report",
    "__end__": "DiagnosisResponse<br/>+ escalation (DA-44)<br/>+ record_incident",
}

# Rotulo da aresta condicional, por no de destino: o valor de agent_domain
# que leva ate ele em app/agent/graph.py::_route_to_specialist.
ROTA = {
    "sap_diagnose": "sap",
    "generic_diagnose": "generic",
    "saas_diagnose": "saas (default)",
}

FORMAS = (
    (False, "Default: GRAPH_RAG_ENABLED=false"),
    (True, "Com GraphRAG: GRAPH_RAG_ENABLED=true"),
)
FORMA_EN = ((False, "Default: GRAPH_RAG_ENABLED=false"),)


def _id(no: str) -> str:
    return {"__start__": "inicio", "__end__": "fim"}.get(no, no)


def mermaid(graph_rag: bool, descricao: dict[str, str] | None = None) -> str:
    """Mermaid de uma forma do grafo, com ordem estavel (diff legivel)."""
    from app.agent import graph as grafo
    from app.config import settings

    anterior = settings.graph_rag_enabled
    settings.graph_rag_enabled = graph_rag
    try:
        g = grafo.build_graph().get_graph()
    finally:
        settings.graph_rag_enabled = anterior

    descricao = descricao or DESCRICAO
    faltando = set(g.nodes) - set(descricao)
    if faltando:
        raise SystemExit(f"no(s) sem descricao em scripts/graph_diagram.py: {sorted(faltando)}")

    linhas = ["flowchart TD"]
    for no in g.nodes:
        texto = descricao[no]
        forma = '(["{}"])' if no in ("__start__", "__end__") else '["{}"]'
        linhas.append(f"    {_id(no)}{forma.format(texto)}")
    for aresta in sorted(g.edges, key=lambda e: (e.source, e.target)):
        origem, destino = _id(aresta.source), _id(aresta.target)
        if aresta.conditional:
            linhas.append(f'    {origem} -.->|"{ROTA[aresta.target]}"| {destino}')
        else:
            linhas.append(f"    {origem} --> {destino}")
    return "\n".join(linhas)


def bloco(
    formas: tuple[tuple[bool, str], ...] = FORMAS, descricao: dict[str, str] | None = None
) -> str:
    partes = [MARCA_INICIO]
    for graph_rag, titulo in formas:
        partes += ["", f"**{titulo}**", "", "```mermaid", mermaid(graph_rag, descricao), "```"]
    partes += ["", MARCA_FIM]
    return "\n".join(partes)


def alvos() -> list[tuple[Path, str]]:
    """(documento, bloco esperado) para cada documento que exibe o grafo."""
    return [
        (DOC, bloco()),
        (README_PT, bloco(FORMAS[:1])),
        (README, bloco(FORMA_EN, DESCRICAO_EN)),
    ]


def _bloco_no_doc(texto: str) -> re.Match[str] | None:
    return re.search(re.escape(MARCA_INICIO) + r".*?" + re.escape(MARCA_FIM), texto, re.DOTALL)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    acao = parser.add_mutually_exclusive_group()
    acao.add_argument("--check", action="store_true")
    acao.add_argument("--write", action="store_true")
    args = parser.parse_args()

    if not (args.check or args.write):
        print(bloco())
        return 0

    falhou = False
    for doc, novo in alvos():
        texto = doc.read_text(encoding="utf-8")
        atual = _bloco_no_doc(texto)
        if atual is None:
            print(f"marcadores nao encontrados em {doc.relative_to(ROOT)}", file=sys.stderr)
            falhou = True
        elif args.check and atual.group(0) != novo:
            print(
                f"{doc.relative_to(ROOT)}: diagrama do grafo desatualizado; "
                "rode scripts/graph_diagram.py --write",
                file=sys.stderr,
            )
            falhou = True
        elif args.write:
            doc.write_text(texto[: atual.start()] + novo + texto[atual.end() :], encoding="utf-8")
    return 1 if falhou else 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
