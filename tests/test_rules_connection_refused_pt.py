"""Regra network_connection_refused cobre relatos em portugues e a
mensagem SAP "Partner not reached" (eval de 26/09/2026: "SM59 dando erro
de conexao recusada" caia no LLM e era confundido com o esgotamento do
pool do gateway RFC)."""

import pytest

from app.agent.rules import match_known_error


@pytest.mark.parametrize(
    "text",
    [
        "SM59 dando erro de conexão recusada",
        "SM59 dando erro de conexao recusada",
        "Conexão foi recusada pelo destino RFC",
        "RFC falha com Partner not reached",
        "connection refused ao chamar destino",
    ],
)
def test_connection_refused_variants_match_rule(text):
    result = match_known_error(text)
    assert result is not None
    assert result["matched_source"] == "rule_engine:network_connection_refused"


def test_gateway_pool_timeout_does_not_match_connection_refused():
    result = match_known_error("RFC SYSTEM_FAILURE apos timeout de 60s no gateway")
    assert result is None or result["rule_engine_category"] != "network_connection_refused"
