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
palavras-chave), nao uma chamada de LLM: mesmo principio da DA-3 do
README.md (guardrails em codigo, nao em prompt), de que decisoes
estruturais pertencem a codigo e nao a autoavaliacao de um modelo -
o roteamento e barato,
explicavel e 100% testavel sem depender de LLM real.
"""

from __future__ import annotations

import re
from typing import Literal

from app.agent.state import CopilotState

AgentDomain = Literal["sap", "saas", "generic"]

# interface_type (ver Literal fechado em app/models.py::IncidentRequest)
# mapeado para o dominio do sub-agente especialista.
# "po" = SAP PO/PI on-premise: middleware SAP de integracao, nao SaaS.
_SAP_INTERFACE_TYPES = {"odata", "rfc", "cap", "po"}
# successfactors: SaaS de RH da SAP, mesmo caso do Ariba — marca SAP,
# produto multi-tenant entregue como servico (OAuth, nao RFC on-premise).
_SAAS_INTERFACE_TYPES = {"servicenow", "salesforce", "workday", "ariba", "successfactors"}

# Heuristica de fallback quando interface_type nao foi informado (fluxo
# livre por descricao textual, ver test_graph_e2e.py casos com
# interface_type=None) - termos que aparecem nos documentos de
# conhecimento SAP deste repositorio — 26 termos cobrindo vocabulario
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
    # Validacao 2026-10-07 (M-01): "fica" saiu - e verbo comum em portugues
    # ("a tela fica lenta") e, por substring, casava ate "verifica". O
    # modulo SAP e escrito FI-CA.
    "fi-ca",
    "successfactors",
    "sfsf",
    "ariba",
    # DA-56: vocabulario de PO/PI. Termos especificos de proposito —
    # "po"/"pi" soltos casariam com palavras portuguesas comuns.
    "process orchestration",
    "process integration",
    "sap po",
    "sap pi",
)

# Validacao 2026-10-07 (M-01): TODOS os termos casam como palavra inteira,
# nao so "sap". Por substring, "rfc" casava "RFC 6749" (OAuth, IETF) e
# termos curtos casavam dentro de palavras comuns ("verifica" -> "fica").
# "Palavra" aqui = nao vizinho de letra/digito, para que "s/4hana",
# "pi/po" e "sap po" continuem casando.
_SAP_WORD_RE = re.compile(r"\bsap\b")
_SAP_KEYWORD_RE = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(re.escape(k) for k in _SAP_KEYWORDS) + r")(?![a-z0-9])"
)
# RFC seguido de numero e especificacao da IETF (RFC 6749, RFC 7231), nao
# Remote Function Call do SAP.
_IETF_RFC_RE = re.compile(r"\brfc[\s-]?\d{3,5}\b")


def _has_sap_signal(description: str) -> bool:
    text = _IETF_RFC_RE.sub(" ", description)
    return bool(_SAP_WORD_RE.search(text) or _SAP_KEYWORD_RE.search(text))


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
    if _has_sap_signal(description):
        return "sap"

    return "generic"


def supervisor_node(state: CopilotState) -> CopilotState:
    """Node de entrada do grafo (DA-22) - roda ANTES do connector_node,
    pois a classificacao so depende de interface_type/description, ja
    presentes na requisicao original. `agent_domain` fica no estado
    para o roteamento condicional em app/agent/graph.py e para
    auditoria/observabilidade (aparece no relatorio final)."""
    return {"agent_domain": classify_domain(state)}
