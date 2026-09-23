"""Supervisor do grafo multi-agente (DA-22).

Antes desta fase, um unico node de diagnostico ("diagnose_node")
atendia qualquer conector com a MESMA persona fixa ("especialista em
integracao SAP"), mesmo para incidentes de ServiceNow, Salesforce,
Workday ou Ariba - um desalinhamento com o principio de design deste
projeto (SAP e um conector entre iguais, nao o eixo arquitetural, ver
README/ARCHITECTURE). O supervisor classifica o DOMINIO do incidente
e o grafo (app/agent/graph.py) roteia para um sub-agente especialista
com persona/expertise apropriada ao dominio - ver
`app/agent/nodes.py::sap_diagnosis_node` / `saas_diagnosis_node`.

Classificacao e DETERMINISTICA (mapeamento de interface_type + poucas
palavras-chave), nao uma chamada de LLM: mesmo principio ja registrado
em `learnings.md` de que guardrails/decisoes estruturais pertencem a
codigo, nao a autoavaliacao de um modelo - o roteamento e barato,
explicavel e 100% testavel sem depender de LLM real.
"""

from __future__ import annotations

import re
from typing import Literal

from app.agent.state import CopilotState

AgentDomain = Literal["sap", "saas", "generic"]

# interface_type (ver Literal fechado em app/models.py::IncidentRequest)
# mapeado para o dominio do sub-agente especialista.
_SAP_INTERFACE_TYPES = {"odata", "rfc", "cap"}
_SAAS_INTERFACE_TYPES = {"servicenow", "salesforce", "workday", "ariba"}

# Heuristica de fallback quando interface_type nao foi informado (fluxo
# livre por descricao textual, ver test_graph_e2e.py casos com
# interface_type=None) - termos que aparecem nos documentos de
# conhecimento SAP deste repositorio — 24 termos cobrindo vocabulario
# de integracao SAP (OData, HANA, BTP, SuccessFactors, Ariba, etc.).
# DA-22 fix: lista expandida para cobrir vocabulario SAP alternativo
# que aparece quando interface_type nao vem preenchido. Termos ordenados
# do mais especifico (sem ambiguidade) para o mais generico.
#
# §3.5: "sap" foi removido desta tupla e tratado separadamente com
# word boundary (\bsap\b). Sem o boundary, "sap" como substring
# disparava falsos positivos em palavras portuguesas comuns:
#   "sapato", "sapiens", "sapphire", "sapatilha", "desapareceu",
#   "desapropriado", etc. Os demais termos da lista sao suficientemente
# especificos para nao ter esse problema (ex.: "iflow", "idoc",
# "s/4hana" nao aparecem em palavras portuguesas aleatorias).
_SAP_KEYWORDS = (
    "iflow",
    "idoc",
    "cpi",
    "rfc",
    "sm59",
    "bapi",
    "abap",
    "btp",
    "s/4hana",
    "s4hana",
    "netweaver",
    "odata",
    "hana",
    "solution manager",
    "solman",
    "pi/po",
    "xi/pi",
    "nwds",
    "fica",
    "fi-tv",
    "successfactors",
    "sfsf",
    "ariba",
)

# Padrao com word boundary para "sap" — evita falsos positivos em
# palavras portuguesas que contem "sap" como substrings
# (sapato, sapiens, desapareceu, etc.).
_SAP_WORD_RE = re.compile(r"\bsap\b")


def classify_domain(state: CopilotState) -> AgentDomain:
    """Decide qual sub-agente especialista deve tratar o incidente.

    Prioridade: interface_type explicito (mais confiavel, vem da
    requisicao estruturada) > palavras-chave na descricao textual >
    "generic" (nenhum sinal de dominio - ainda assim precisa de UM
    especialista, o generalista multi-fornecedor cobre esse caso)."""
    interface_type = (state.get("interface_type") or "").lower()
    if interface_type in _SAP_INTERFACE_TYPES:
        return "sap"
    if interface_type in _SAAS_INTERFACE_TYPES:
        return "saas"

    description = (state.get("description") or "").lower()
    # §3.5: "sap" verificado com word boundary; demais keywords por
    # substring (sao especificos o suficiente para nao ter ambiguidade).
    if _SAP_WORD_RE.search(description) or any(keyword in description for keyword in _SAP_KEYWORDS):
        return "sap"

    return "generic"


def supervisor_node(state: CopilotState) -> CopilotState:
    """Node de entrada do grafo (DA-22) - roda ANTES do connector_node,
    pois a classificacao so depende de interface_type/description, ja
    presentes na requisicao original. `agent_domain` fica no estado
    para o roteamento condicional em app/agent/graph.py e para
    auditoria/observabilidade (aparece no relatorio final)."""
    return {"agent_domain": classify_domain(state)}
