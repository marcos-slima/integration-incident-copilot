"""DA-22 (Multi-agent): classificacao deterministica de dominio feita
pelo supervisor - sem LLM, 100% testavel. Cobre a prioridade
interface_type > palavra-chave na descricao > generic."""

import pytest

from app.agent.supervisor import classify_domain, supervisor_node


def test_classify_domain_from_sap_interface_type():
    for interface_type in ("odata", "rfc", "cap", "po"):
        assert classify_domain({"interface_type": interface_type, "description": ""}) == "sap"


def test_classify_domain_from_saas_interface_type():
    for interface_type in ("servicenow", "salesforce", "workday", "ariba"):
        assert classify_domain({"interface_type": interface_type, "description": ""}) == "saas"


def test_classify_domain_is_case_insensitive():
    assert classify_domain({"interface_type": "RFC", "description": ""}) == "sap"


def test_classify_domain_falls_back_to_sap_keyword_in_description():
    state = {"interface_type": None, "description": "IDoc travado com status 51 no CPI"}
    assert classify_domain(state) == "sap"


def test_classify_domain_falls_back_to_generic_without_any_signal():
    state = {"interface_type": None, "description": "algo estranho aconteceu no sistema"}
    assert classify_domain(state) == "generic"


def test_classify_domain_generic_when_interface_type_not_recognized():
    # interface_type fora dos dois conjuntos conhecidos (ex: apim, nao
    # exposto em IncidentRequest.interface_type, mas alcancavel via
    # get_connector() direto) cai em generic, nao quebra.
    state = {"interface_type": "apim", "description": ""}
    assert classify_domain(state) == "generic"


def test_supervisor_node_returns_agent_domain_key():
    result = supervisor_node({"interface_type": "salesforce", "description": ""})
    assert result == {"agent_domain": "saas"}


# ── DA-22 fix: keywords expandidos ──────────────────────────────────────────
def test_classify_domain_keyword_odata():
    assert (
        classify_domain({"interface_type": None, "description": "Erro na query OData /SalesOrders"})
        == "sap"
    )


def test_classify_domain_keyword_hana():
    assert (
        classify_domain({"interface_type": None, "description": "HANA connection timeout"}) == "sap"
    )


def test_classify_domain_keyword_s4hana():
    assert (
        classify_domain({"interface_type": None, "description": "falha ao integrar com S4HANA"})
        == "sap"
    )


def test_classify_domain_keyword_solution_manager():
    assert (
        classify_domain({"interface_type": None, "description": "alerta do Solution Manager"})
        == "sap"
    )


def test_classify_domain_keyword_solman():
    assert (
        classify_domain({"interface_type": None, "description": "ticket aberto via SolMan"})
        == "sap"
    )


def test_classify_domain_keyword_successfactors():
    assert (
        classify_domain(
            {"interface_type": None, "description": "replicacao SuccessFactors para S/4"}
        )
        == "sap"
    )


def test_classify_domain_keyword_sfsf():
    assert classify_domain({"interface_type": None, "description": "erro na API SFSF EC"}) == "sap"


def test_classify_domain_keyword_ariba():
    assert (
        classify_domain({"interface_type": None, "description": "PO Ariba nao replicou"}) == "sap"
    )


def test_classify_domain_keyword_case_insensitive_odata():
    assert (
        classify_domain({"interface_type": None, "description": "ODATA endpoint retornou 500"})
        == "sap"
    )


# ── DA-22 fix: nó generic_diagnosis_node existe e roteia corretamente ───────
def test_supervisor_node_returns_generic_for_unknown():
    result = supervisor_node({"interface_type": None, "description": "falha desconhecida"})
    assert result == {"agent_domain": "generic"}


def test_supervisor_node_returns_sap_for_odata_keyword():
    result = supervisor_node({"interface_type": None, "description": "erro na query OData v4"})
    assert result == {"agent_domain": "sap"}


# ── DA-56: SAP PO/PI e' middleware SAP on-premise, nao SaaS ─────────────────
def test_classify_domain_po_e_sap_e_nao_saas():
    """PO/PI poderia ser classificado como SaaS por ser produto SAP — mas e'
    on-premise (RFC/ICM, nao OAuth multi-tenant). Roteando errado, o
    incidente iria para o sub-agente de SaaS, que assume produto na nuvem."""
    assert classify_domain({"interface_type": "po", "description": ""}) == "sap"


def test_classify_domain_po_por_descricao_livre():
    """Sem interface_type, o vocabulario de PO/PI tem de puxar para `sap`.
    Os termos sao especificos de proposito: "po"/"pi" soltos casariam com
    palavras portuguesas comuns."""
    assert (
        classify_domain({"interface_type": None, "description": "SAP PO mensagem FAILED"}) == "sap"
    )
    assert (
        classify_domain(
            {"interface_type": None, "description": "interface travada no Process Orchestration"}
        )
        == "sap"
    )


# ---------------------------------------------------------------------------
# Validacao 2026-10-07 (M-01): termos casam como palavra inteira
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "description",
    [
        "O usuario verifica o pedido e a tela fica lenta no portal Salesforce",
        "Falha OAuth conforme RFC 6749 ao chamar API do Workday",
        "Johanna reportou erro no webhook",
        "o cliente desapareceu do cadastro",
        "erro no rfc-7231 de cache HTTP",
    ],
)
def test_m01_sem_falso_positivo_sap(description):
    assert classify_domain({"interface_type": None, "description": description}) == "generic"


@pytest.mark.parametrize(
    "description",
    [
        "destino RFC SM59 sem resposta",
        "chamada RFC falhou com timeout",
        "conta contrato no FI-CA nao compensou",
        "falha ao integrar com S/4HANA",
        "mensagem presa no PI/PO",
        "BAPI_IDOC_STATUS retornou erro",
    ],
)
def test_m01_termos_sap_continuam_casando(description):
    assert classify_domain({"interface_type": None, "description": description}) == "sap"
