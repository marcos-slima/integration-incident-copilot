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
