"""Negociação de contratos públicos entre core, produtos e UI."""

import sys
import types

import pytest
from fastapi.testclient import TestClient

from app import sdk
from app.agent.domain_plugins import DomainPluginError, load_plugins
from app.main import app


@pytest.mark.parametrize(
    "required,accepted",
    [
        ("1.0", True),
        ("1.1", False),
        ("2.0", False),
        ("0.0", False),
        ("invalid", False),
        ("1", False),
        ("1.0.0", False),
        ("1.-1", False),
    ],
)
def test_sdk_compatibilidade(required, accepted):
    assert sdk.compatible_version(required, sdk.SDK_CONTRACT_VERSION) is accepted


def test_http_rejeita_major_incompativel():
    response = TestClient(app).get("/health", headers={"X-Core-Contract-Version": "2.0"})
    assert response.status_code == 409
    assert response.json()["code"] == "incompatible_contract"
    assert response.headers["X-Core-Contract-Version"] == "1.0"


def test_http_aceita_v1_e_cliente_legado():
    client = TestClient(app)
    for headers in ({}, {"X-Core-Contract-Version": "1.0"}):
        response = client.get("/auth/session", headers=headers)
        assert response.status_code == 200
        assert response.headers["X-Core-Contract-Version"] == "1.0"


def test_plugin_incompativel_falha_antes_de_executar(monkeypatch):
    class Plugin:
        contract_version = "2.0"
        name = "future"

        def context_schema(self):
            return {}

        def validate_context(self, context):
            return context

        def run(self, state):
            raise AssertionError("não deve executar")

    module = types.ModuleType("future_contract_plugin")
    module.PLUGIN = Plugin()
    monkeypatch.setitem(sys.modules, module.__name__, module)
    load_plugins.cache_clear()
    try:
        with pytest.raises(DomainPluginError, match="incompatível"):
            load_plugins(module.__name__)
    finally:
        load_plugins.cache_clear()
