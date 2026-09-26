"""Recuperacao de matched_source quando o structured output o perde.

Caso real (26/09/2026, qwen3-coder-next via Ollama): o texto final do
agente continha "matched_source": "cpi_http_401.md", mas o
structured_response devolvia None - 4 falhas no promptfoo com
diagnostico correto.
"""

from app.agent.nodes import _recover_matched_source_from_raw


def test_recovers_source_from_raw_json():
    raw = '{\n  "matched_source": "cpi_http_401.md",\n  "probable_root_cause": "token expirado"\n}'
    assert _recover_matched_source_from_raw(raw) == "cpi_http_401.md"


def test_returns_none_when_raw_has_null():
    raw = '{"matched_source": null, "probable_root_cause": "x"}'
    assert _recover_matched_source_from_raw(raw) is None


def test_returns_none_when_field_absent_or_empty():
    assert _recover_matched_source_from_raw("sem json aqui") is None
    assert _recover_matched_source_from_raw('{"matched_source": ""}') is None
    assert _recover_matched_source_from_raw("") is None
