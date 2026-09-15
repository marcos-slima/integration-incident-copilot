"""Testes de integracao end-to-end do grafo LangGraph completo:
conector -> retrieve -> diagnose -> report.

Precisam da stack completa: Qdrant (com sap_incident_docs indexada) e
Ollama (com o modelo LLM_MODEL e nomic-embed-text baixados).

Formaliza os 7 cenarios validados manualmente durante o
desenvolvimento, incluindo o caso de seguranca do identificador
desconhecido (nao deve alucinar um diagnostico especifico).
"""

import pytest

from app.agent.graph import run_diagnosis
from app.models import IncidentRequest


@pytest.mark.integration
@pytest.mark.parametrize(
    "description,interface_type,identifier,expected_source,min_confidence",
    [
        ("iFlow falhando com erro 401", None, None, "cpi_http_401.md", 0.5),
        ("investigar falha reportada no iFlow", "odata", "CPI-401-DEMO", "cpi_http_401.md", 0.7),
        ("IDoc travado", "rfc", "RFC-IDOC-51-DEMO", "idoc_status_51.md", 0.7),
        ("SM59 não conecta", "rfc", "RFC-CONN-REFUSED-DEMO", "rfc_connection_refused.md", 0.7),
        ("iFlow travando ao consumir OData sem retorno", None, None, "odata_timeout_cpi.md", 0.5),
        ("IDoc parado com status 51", None, None, "idoc_status_51.md", 0.5),
        ("SM59 dando erro de conexão recusada", None, None, "rfc_connection_refused.md", 0.5),
    ],
)
def test_diagnosis_matches_expected_source(
    description, interface_type, identifier, expected_source, min_confidence
):
    request = IncidentRequest(
        description=description,
        interface_type=interface_type,
        identifier=identifier,
    )
    result = run_diagnosis(request)

    assert result.matched_source == expected_source, (
        f"Esperado fonte '{expected_source}' para '{description}' "
        f"(interface={interface_type}, id={identifier}), "
        f"veio '{result.matched_source}'"
    )
    assert result.confidence >= min_confidence
    assert result.probable_root_cause and result.probable_root_cause != "N/A"
    assert result.report_markdown  # relatorio nao pode vir vazio


@pytest.mark.integration
def test_unknown_identifier_does_not_hallucinate_specific_diagnosis():
    """Caso de seguranca: identificador desconhecido nao deve produzir
    um diagnostico especifico com alta confianca - o sistema deve
    reconhecer que nao tem base solida, nao inventar uma causa raiz.
    """
    request = IncidentRequest(
        description="algo estranho aconteceu",
        interface_type="odata",
        identifier="XPTO-999-NAO-EXISTE",
    )
    result = run_diagnosis(request)

    assert result.confidence < 0.6, (
        f"Identificador desconhecido nao deveria gerar alta confianca (veio {result.confidence})"
    )
