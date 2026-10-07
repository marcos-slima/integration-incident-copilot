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


@pytest.mark.parametrize(
    ("doc", "esperado"), graph_diagram.alvos(), ids=["ARCHITECTURE", "README.pt-BR", "README"]
)
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
    assert nos <= set(graph_diagram.DESCRICAO_EN)


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


# ---------------------------------------------------------------------------
# Linhagem de evidencia, implantacao e pipeline de avaliacao
# ---------------------------------------------------------------------------


def test_linhagem_cita_todos_os_trust_levels_e_tetos():
    from app.agent import nodes

    secao = _secao(ARQ, "## Linhagem de evidencia e confianca")
    niveis = set(
        re.findall(r'"trust_level":\s*"(\w+)"', inspect.getsource(nodes._assemble_evidence))
    )
    niveis |= {"simulated", "system_observed"}  # o conector escolhe entre os dois
    assert all(n in secao for n in niveis)
    fonte = inspect.getsource(nodes._apply_confidence_guardrails)
    tetos = set(re.findall(r"min\(model_confidence, (0\.\d)\)", fonte))
    assert tetos == {"0.4", "0.3"}
    for t in tetos:
        assert t.replace(".", ",") in secao
    assert f"{nodes.EVIDENCE_CONFIDENCE_MARGIN:.2f}".replace(".", ",") in secao


def test_perfis_do_compose_na_tabela_de_implantacao():
    import yaml

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    secao = _secao(ARQ, "### Local (`docker-compose.yml`)")
    for nome, servico in compose["services"].items():
        perfil = (servico.get("profiles") or ["(nenhum)"])[0]
        linha = next(
            (
                linha
                for linha in secao.splitlines()
                if linha.startswith(f"| `{perfil}`")
                or (perfil == "(nenhum)" and linha.startswith("| (nenhum)"))
            ),
            None,
        )
        assert linha is not None, f"perfil {perfil} fora da tabela"
        assert f"`{nome}`" in linha, f"servico {nome} fora da linha do perfil {perfil}"


def test_diagrama_kyma_bate_com_os_manifests():
    import yaml

    secao = _secao(ARQ, "### SAP BTP Kyma (`deploy/kyma/`)")
    bloco = _mermaid(secao)[0]
    kyma = ROOT / "deploy" / "kyma"
    kinds = {
        yaml.safe_load((kyma / r).read_text(encoding="utf-8"))["kind"]
        for r in yaml.safe_load((kyma / "kustomization.yaml").read_text(encoding="utf-8"))[
            "resources"
        ]
    }
    assert kinds - {"Namespace", "HorizontalPodAutoscaler"} <= {k for k in kinds if k in bloco}
    cm = yaml.safe_load((kyma / "configmap.yaml").read_text(encoding="utf-8"))["data"]
    for chave in re.findall(r"([A-Z_]+)=", bloco):
        assert chave in cm, f"{chave} citado no diagrama nao esta no ConfigMap"
        assert f"{chave}={cm[chave]}" in bloco, f"valor de {chave} divergente"
    hpa = yaml.safe_load((kyma / "hpa.yaml").read_text(encoding="utf-8"))["spec"]
    assert f"{hpa['minReplicas']} a {hpa['maxReplicas']} pods" in bloco


def test_rota_do_llm_no_kyma_e_a_documentada():
    """O texto diz que, com a configuracao do ConfigMap, dado confidencial
    so vai para o Azure OpenAI. Se o ConfigMap mudar, o texto precisa mudar."""
    import yaml

    from app.config import Settings
    from app.llm import gateway

    cm = yaml.safe_load((ROOT / "deploy/kyma/configmap.yaml").read_text(encoding="utf-8"))["data"]
    valores = {k.lower(): v for k, v in cm.items() if k.lower() in Settings.model_fields}
    origem = "https://exemplo.openai.azure.com"
    valores.update(
        confidential_allowed_origins=origem,
        azure_openai_endpoint=origem,
        azure_openai_api_key="x",
        azure_openai_deployment="d",
        openai_api_key="y",
    )
    cfg = Settings(_env_file=None, **valores)
    sel = gateway._select_allowed_providers
    assert sel("public", cfg.llm_provider, cfg.llm_fallback_provider, cfg) == [
        "openai",
        "azure_openai",
    ]
    assert sel("confidential", cfg.llm_provider, cfg.llm_fallback_provider, cfg) == ["azure_openai"]
    assert "confidential -> [azure_openai]" in ARQ


def test_pipeline_de_avaliacao_bate_com_os_workflows():
    import yaml

    from app.evaluation.gates import GATES

    qg = (ROOT / "docs" / "QUALITY_GATES.md").read_text(encoding="utf-8")
    bloco = _mermaid(_secao(qg, "## Pipeline de avaliacao"))[0]
    jobs: set[str] = set()
    for wf in ("tests.yml", "quality.yml"):
        dados = yaml.safe_load((ROOT / ".github" / "workflows" / wf).read_text(encoding="utf-8"))
        jobs |= set(dados["jobs"])
    no_diagrama = set(re.findall(r"<b>([a-z0-9_-]+)</b>", bloco))
    assert no_diagrama == jobs
    assert f"{len(GATES)} gates" in bloco
    for artefato in re.findall(r"(data/[\w/.]+\.json)", bloco):
        if "index_manifest" not in artefato:  # gerado na maquina do usuario
            assert (ROOT / artefato).exists() or artefato.endswith("promptfoo_baseline.json"), (
                artefato
            )
