"""DA-22 (Multi-agent): classificacao deterministica de dominio feita
pelo supervisor - sem LLM, 100% testavel. Cobre a prioridade
interface_type > palavra-chave na descricao > generic."""

from app.agent.supervisor import classify_domain, supervisor_node


def test_classify_domain_from_sap_interface_type():
    for interface_type in ("odata", "rfc", "cap"):
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
