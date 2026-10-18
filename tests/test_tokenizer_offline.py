"""DA-67: o tokenizer nunca derruba o diagnostico.

`_estimate_tokens` usa tiktoken (cl100k_base) quando disponivel e cai na
heuristica len//4 para QUALQUER falha de carga - tiktoken ausente ou BPE
sem rede (container/Kyma). A falha e lembrada para nao repetir a tentativa
(e o timeout de rede) a cada chamada.
"""

import builtins
import logging

import pytest

import app.llm.gateway as gw

TEXT = "Hello world, this is a test."  # 28 chars -> heuristica = 7


@pytest.fixture(autouse=True)
def _reset_tokenizer_state(monkeypatch):
    monkeypatch.setattr(gw, "_encoding_cache", None)
    monkeypatch.setattr(gw, "_encoding_failed", False)


def _heuristic(text: str) -> int:
    return max(1, len(text) // 4)


def test_uses_tiktoken_when_available():
    tiktoken = pytest.importorskip("tiktoken")
    try:
        expected = len(tiktoken.get_encoding("cl100k_base").encode(TEXT, allowed_special="all"))
    except Exception:  # noqa: BLE001 - BPE indisponivel no ambiente de teste
        pytest.skip("BPE do cl100k_base indisponivel (sem rede/cache)")
    gw._encoding_cache = None
    gw._encoding_failed = False
    assert gw._estimate_tokens(TEXT) == expected


@pytest.mark.parametrize(
    "exc", [ConnectionError("network unreachable"), OSError("no cache"), ValueError("bad")]
)
def test_load_failure_falls_back_to_heuristic(monkeypatch, exc):
    tiktoken = pytest.importorskip("tiktoken")

    def boom(_name):
        raise exc

    monkeypatch.setattr(tiktoken, "get_encoding", boom)
    assert gw._estimate_tokens(TEXT) == _heuristic(TEXT)


def test_import_error_falls_back_to_heuristic(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "tiktoken":
            raise ImportError("tiktoken not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert gw._estimate_tokens(TEXT) == _heuristic(TEXT)


def test_failure_is_remembered_and_logged_once(monkeypatch, caplog):
    tiktoken = pytest.importorskip("tiktoken")
    calls = {"n": 0}

    def boom(_name):
        calls["n"] += 1
        raise ConnectionError("network unreachable")

    monkeypatch.setattr(tiktoken, "get_encoding", boom)
    with caplog.at_level(logging.WARNING, logger=gw.logger.name):
        for _ in range(5):
            assert gw._estimate_tokens(TEXT) == _heuristic(TEXT)

    assert calls["n"] == 1, "a carga do BPE nao deve ser retentada a cada chamada"
    warnings = [r for r in caplog.records if "tiktoken indisponivel" in r.getMessage()]
    assert len(warnings) == 1


def test_encode_failure_falls_back(monkeypatch):
    class BrokenEncoding:
        def encode(self, *_a, **_k):
            raise RuntimeError("encode failed")

    monkeypatch.setattr(gw, "_encoding_cache", BrokenEncoding())
    assert gw._estimate_tokens(TEXT) == _heuristic(TEXT)


def test_minimum_one_token():
    gw._encoding_failed = True
    assert gw._estimate_tokens("") == 1
