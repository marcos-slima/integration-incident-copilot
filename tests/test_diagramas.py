"""Diagramas da documentacao amarrados ao codigo (validacao 2026-10-07).

Os docs/UC_* removidos no Bloco 5 desenhavam um grafo que nao existia. Aqui:
- o grafo de orquestracao em docs/ARCHITECTURE.md e GERADO, e o bloco do
  documento tem de ser identico ao que scripts/graph_diagram.py produz;
- todo no do grafo tem descricao, e o rotulo das arestas condicionais bate
  com app/agent/graph.py::_route_to_specialist;
- os estados das maquinas de estado sao exatamente os do codigo;
- o diagrama de estado cita todos os campos de CopilotState, e cada no
  escreve o campo que o diagrama diz;
- os participantes das sequencias existem.
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ARQ = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
CASOS = (ROOT / "docs" / "CASOS_DE_USO.md").read_text(encoding="utf-8")

sys.path.insert(0, str(ROOT / "scripts"))
import graph_diagram


def _secao(texto: str, titulo: str) -> str:
    """Conteudo de uma secao ### ou ## ate o proximo cabecalho do mesmo nivel."""
    nivel = titulo.split(" ", 1)[0]
    inicio = texto.index(titulo)
    resto = texto[inicio + len(titulo) :]
    fim = re.search(rf"^{nivel} |^## ", resto, flags=re.MULTILINE)
    return resto[: fim.start()] if fim else resto


def _mermaid(texto: str) -> list[str]:
    return re.findall(r"```mermaid\n(.*?)```", texto, flags=re.DOTALL)


# ---------------------------------------------------------------------------
# Grafo gerado
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("doc", "esperado"), graph_diagram.alvos(), ids=["ARCHITECTURE", "README"])
def test_bloco_do_grafo_no_doc_e_o_gerado(doc, esperado):
    atual = re.search(
        re.escape(graph_diagram.MARCA_INICIO) + r".*?" + re.escape(graph_diagram.MARCA_FIM),
        doc.read_text(encoding="utf-8"),
        flags=re.DOTALL,
    )
    assert atual is not None, f"marcadores do grafo gerado sumiram de {doc.name}"
    assert atual.group(0) == esperado, "rode: uv run python scripts/graph_diagram.py --write"


@pytest.mark.parametrize("graph_rag", [False, True])
def test_todo_no_do_grafo_tem_descricao(graph_rag):
    from app.agent import graph
    from app.config import settings

    anterior = settings.graph_rag_enabled
    settings.graph_rag_enabled = graph_rag
    try:
        nos = set(graph.build_graph().get_graph().nodes)
    finally:
        settings.graph_rag_enabled = anterior
    assert nos <= set(graph_diagram.DESCRICAO)


def test_rotulo_das_arestas_condicionais_bate_com_o_roteador():
    from app.agent.graph import _route_to_specialist

    for destino, rotulo in graph_diagram.ROTA.items():
        dominio = rotulo.split(" ", 1)[0]
        assert _route_to_specialist({"agent_domain": dominio}) == destino
    # "(default)": qualquer outro valor cai em saas
    assert _route_to_specialist({"agent_domain": "qualquer"}) == "saas_diagnose"


# ---------------------------------------------------------------------------
# Contrato de estado
# ---------------------------------------------------------------------------


def test_diagrama_de_estado_cita_todos_os_campos_de_copilotstate():
    from app.agent.state import CopilotState

    bloco = _mermaid(_secao(ARQ, "## Contrato de estado e memoria"))[0]
    faltando = [c for c in CopilotState.__annotations__ if c not in bloco]
    assert not faltando, f"campos de CopilotState fora do diagrama: {faltando}"


@pytest.mark.parametrize(
    ("no", "campo"),
    [
        ("supervisor_node", "agent_domain"),
        ("connector_node", "connector_data"),
        ("retrieve_node", "retrieved_context"),
        ("graph_enrich_node", "graph_history"),
        ("report_node", "report_markdown"),
    ],
)
def test_cada_no_escreve_o_campo_do_diagrama(no, campo):
    from app.agent import nodes, supervisor

    fn = getattr(nodes, no, None) or getattr(supervisor, no)
    assert re.search(rf'return \{{\s*"{campo}"', inspect.getsource(fn))


def test_graph_write_nao_escreve_no_estado():
    from app.agent.nodes import graph_write_node

    assert "return {}" in inspect.getsource(graph_write_node)


def test_web_search_node_continua_fora_do_grafo():
    """O doc registra web_search_node como codigo nao ligado ao grafo. Se ele
    entrar no grafo, o achado e o diagrama de estado precisam ser revistos."""
    from app.agent import graph

    for flag in (False, True):
        graph.settings.graph_rag_enabled = flag
        try:
            assert "web_search" not in graph.build_graph().get_graph().nodes
        finally:
            graph.settings.graph_rag_enabled = False


# ---------------------------------------------------------------------------
# Maquinas de estado e decisao
# ---------------------------------------------------------------------------


def _estados(bloco: str) -> set[str]:
    estados: set[str] = set()
    for linha in bloco.splitlines():
        m = re.match(r"\s*(\[\*\]|\w+)\s*-->\s*(\[\*\]|\w+)", linha)
        if m:
            estados |= {m.group(1), m.group(2)}
    return estados - {"[*]"}


def test_maquina_da_observacao_de_contrato():
    from app.contracts.diff import ObservationStatus

    bloco = _mermaid(_secao(ARQ, "### Observacao de contrato (DA-52)"))[0]
    assert _estados(bloco) - {"leitura"} == {s.value for s in ObservationStatus}


def test_decisao_de_escalonamento_lista_todas_as_razoes():
    from app.agent.escalation import (
        CURATED_TIER_MIN_EVIDENCE,
        FLOOR_TIER_MIN_EVIDENCE,
        Reason,
    )

    bloco = _mermaid(_secao(ARQ, "### Escalonamento (DA-44)"))[0]
    razoes = {v for k, v in vars(Reason).items() if k.isupper()}
    assert all(r in bloco for r in razoes)
    for limiar in (FLOOR_TIER_MIN_EVIDENCE, CURATED_TIER_MIN_EVIDENCE):
        assert f"{limiar:.2f}".replace(".", ",") in bloco


def test_maquina_da_task_a2a():
    from app.a2a import task_manager

    bloco = _mermaid(_secao(ARQ, "### Task A2A (DA-14)"))[0]
    fonte = inspect.getsource(task_manager)
    no_codigo = set(re.findall(r'state(?:: str)? = "(\w+)"', fonte))
    assert _estados(bloco) == no_codigo
    assert task_manager.TERMINAL_STATES <= _estados(bloco)


def test_maquina_do_usuario_da_ui():
    from app import webusers

    bloco = _mermaid(_secao(ARQ, "### Usuario da UI (DA-55)"))[0]
    no_codigo = {v for k, v in vars(webusers).items() if k.startswith("STATUS_")}
    assert _estados(bloco) == no_codigo


def test_maquina_da_idempotencia_cita_as_funcoes_reais():
    from app.events import idempotency

    secao = _secao(ARQ, "### Evento recebido (idempotencia)")
    for fn in ("is_duplicate", "mark_completed", "release"):
        assert fn in secao and hasattr(idempotency, fn)


# ---------------------------------------------------------------------------
# Sequencias
# ---------------------------------------------------------------------------


def test_sequencias_citam_simbolos_que_existem():
    from app.agent import escalation, nodes
    from app.services import incident_recorder

    seq = "\n".join(_mermaid(CASOS))
    for nome, modulo in [
        ("compute_escalation_signal", escalation),
        ("_assemble_evidence", nodes),
        ("_apply_confidence_guardrails", nodes),
        ("_sanitize_web_search_query", nodes),
        ("_web_search_allowed", nodes),
        ("_fallback_diagnosis", nodes),
        ("record_incident", incident_recorder),
    ]:
        assert nome in seq, nome
        assert hasattr(modulo, nome), nome


def test_limite_do_react_citado_e_o_default_do_settings():
    from app.config import Settings

    padrao = Settings.model_fields["react_agent_recursion_limit"].default
    assert f"REACT_AGENT_RECURSION_LIMIT (default {padrao})" in CASOS


def test_documentacao_nao_tem_mais_diagrama_ascii():
    for texto in (ARQ, CASOS):
        assert "──►" not in texto
