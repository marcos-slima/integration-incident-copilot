import importlib.util
import json
import re
import sys
from importlib.metadata import distribution
from pathlib import Path

from app import sdk
from fastapi.testclient import TestClient

from iic_copilot.main import app

ROOT = Path(__file__).resolve().parents[1]


def test_installed_shell_and_provider():
    import app as core

    import iic_copilot

    for module in (iic_copilot, core):
        assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    assert importlib.util.find_spec("dfe_copilot") is None
    assert not (ROOT / "app").exists()
    assert (
        not json.loads(distribution("copilot-core").read_text("direct_url.json"))
        .get("dir_info", {})
        .get("editable", False)
    )


def test_ui_offers_the_public_transport_contract():
    request = sdk.incident_request(description="contract probe")
    field = request.model_json_schema()["properties"]["interface_type"]
    accepted = set()
    for variant in field.get("anyOf", [field]):
        accepted.update(variant.get("enum", []))
    text = (ROOT / "frontend/src/components/DiagnoseView.tsx").read_text()
    block = re.search(r"const SYSTEMS[^=]*=\s*\[(.*?)\];", text, re.DOTALL)
    assert block is not None
    offered = {x for x in re.findall(r"\[\s*'([a-z_]*)'\s*,", block.group(1)) if x}
    assert accepted == offered


def test_http_and_ui():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/").status_code == 200
        response = client.post(
            "/diagnose", json={"description": "contract probe", "interface_type": "not-supported"}
        )
        assert response.status_code == 422
