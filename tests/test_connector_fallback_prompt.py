"""Conector em fallback (identificador nao reconhecido) nao pode injetar
dados fabricados no prompt nem no relatorio.

Caso real: POST /diagnose sem `identifier` fez o mock OData devolver o
_DEFAULT (status=error, codigo=500 simulado). Mesmo com o aviso de
fallback no prompt, o LLM construiu a causa raiz sobre o "500" inventado
(diagnosis_confidence 0.33, causa errada). A correcao remove status,
codigo, mensagem e raw do prompt quando `is_fallback=True`.
"""

from app.agent.nodes import _build_diagnosis_prompt
from app.connectors.base import ConnectorResult


def _state(connector_data):
    return {
        "description": "Chamada OData do CPI para o S/4HANA excede o tempo limite",
        "retrieved_context": [],
        "connector_data": connector_data,
    }


def test_fallback_connector_does_not_leak_fabricated_error_into_prompt():
    fallback = ConnectorResult(
        source_system="OData",
        status="error",
        error_code="500",
        message="Erro generico simulado ao consumir servico OData (identificador nao reconhecido)",
        raw="HTTP/1.1 500 Internal Server Error (dados mock, identificador desconhecido)",
        is_fallback=True,
    )

    prompt = _build_diagnosis_prompt(_state(fallback), persona="sap")

    assert "NENHUM DADO DISPONIVEL" in prompt
    assert "500" not in prompt
    assert "Internal Server Error" not in prompt
    assert "codigo de erro:" not in prompt


def test_recognized_connector_data_still_reaches_prompt():
    scenario = ConnectorResult(
        source_system="OData",
        status="error",
        error_code="504",
        message="Timeout ao consumir servico OData a partir de iFlow CPI",
        raw="HTTP/1.1 504 Gateway Timeout\nMPL Status: FAILED",
    )

    prompt = _build_diagnosis_prompt(_state(scenario), persona="sap")

    assert "codigo de erro: 504" in prompt
    assert "MPL Status: FAILED" in prompt
    assert "NENHUM DADO DISPONIVEL" not in prompt
