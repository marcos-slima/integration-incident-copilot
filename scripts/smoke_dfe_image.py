"""Smoke isolado da imagem DF-e, com credenciais efêmeras e XML sintético."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.auth import hash_password

ROOT = Path(__file__).resolve().parents[1]


def docker(*args: str, data: str | None = None) -> str:
    return subprocess.run(
        ["docker", *args],
        input=data,
        text=True,
        capture_output=True,
        check=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("DFE_")},
    ).stdout.strip()


def main(image: str, compose: bool = False) -> None:
    password = secrets.token_urlsafe(24)
    env = {
        name: secrets.token_urlsafe(32)
        for name in (
            "API_KEY",
            "A2A_API_KEY",
            "EVENT_MESH_API_KEY",
            "ADMIN_API_KEY",
            "SESSION_SECRET",
        )
    }
    env.update(
        {
            "WEB_UI_USERS": "smoke:" + hash_password(password),
            "REQUIRE_AUTH": "true",
            "DATABASE_URL": "",
            "REDIS_URL": "",
            "GRAPH_RAG_ENABLED": "false",
            "CHECKPOINTING_ENABLED": "false",
            "AMQP_ENABLED": "false",
            "WEB_SEARCH_ENABLED": "false",
            "DFE_KB_ENABLED": "false",
            "QDRANT_URL": "http://127.0.0.1:9",
            "OLLAMA_HOST": "http://127.0.0.1:9",
        }
    )
    jar = http.cookiejar.CookieJar()
    client = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar)
    )
    with tempfile.TemporaryDirectory(prefix="dfe-smoke-") as scratch:
        env_file = Path(scratch) / "runtime.env"
        compose_args = None
        if compose:
            env = {"DFE_" + key: value for key, value in env.items()}
            env.update({"DFE_IMAGE": image, "DFE_PORT": "0"})
            compose_args = [
                "compose",
                "-f",
                str(ROOT / "packages/dfe/compose.yaml"),
                "--project-name",
                "dfe-smoke-" + secrets.token_hex(5),
                "--env-file",
                str(env_file),
            ]
        env_file.write_text("\n".join(f"{k}={v}" for k, v in env.items()) + "\n")
        env_file.chmod(0o600)
        container = None
        try:
            if compose_args:
                docker(
                    *compose_args, "up", "--detach", "--no-build", "--wait", "--wait-timeout", "90"
                )
                container = docker(*compose_args, "ps", "--quiet", "dfe")
            else:
                container = docker(
                    "run",
                    "--rm",
                    "--detach",
                    "--env-file",
                    str(env_file),
                    "--publish",
                    "127.0.0.1::8000",
                    image,
                )
            port = docker("port", container, "8000/tcp").rsplit(":", 1)[1]
            base = f"http://127.0.0.1:{port}"

            def request(path, body=None):
                data = json.dumps(body).encode() if body is not None else None
                req = urllib.request.Request(
                    base + path, data=data, headers={"Content-Type": "application/json"}
                )
                return client.open(req, timeout=5)

            deadline = time.monotonic() + 90
            while True:
                try:
                    with request("/health") as response:
                        assert json.load(response) == {"status": "ok"}
                    break
                except (OSError, urllib.error.URLError):
                    if time.monotonic() > deadline:
                        raise RuntimeError("Imagem não respondeu ao healthcheck") from None
                    time.sleep(0.5)
            try:
                request("/domain-plugins")
                raise AssertionError("Catálogo não deve ser público")
            except urllib.error.HTTPError as error:
                assert error.code == 401
            with request("/auth/login", {"username": "smoke", "password": password}) as response:
                assert json.load(response)["authenticated"] is True
                assert "HttpOnly" in response.headers["Set-Cookie"]
                expected_cookie = "dfe_container_session" if compose else "dfe_session"
                assert response.headers["Set-Cookie"].startswith(expected_cookie + "=")
            with request("/domain-plugins") as response:
                catalog = json.load(response)
                assert catalog["contract_version"] == "1.0"
                assert [plugin["name"] for plugin in catalog["plugins"]] == ["dfe"]
            with request("/") as response:
                html = response.read().decode()
                assert "DF-e Copilot" in html
            assets = re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"', html)
            assert assets, "Bundle não referenciado no HTML"
            for asset in assets:
                with request(asset) as response:
                    assert response.status == 200
                    assert b"Integration Incident Copilot" not in response.read()
            try:
                request(
                    "/diagnose",
                    {
                        "description": "Teste sintético",
                        "domain_context": {"environment": "invalid"},
                    },
                )
                raise AssertionError("Contexto fiscal inválido foi aceito")
            except urllib.error.HTTPError as error:
                assert error.code == 422
            code = """
import importlib.util, json, os, sys
from pathlib import Path
from dfe_copilot.resources import DATA_DIR, UI_DIR
from dfe_copilot.fiscal.xml_tools import SchemaPackage, analyze_fiscal_xml
assert os.getuid() != 0
assert not Path('/app/data/sample_docs').exists()
assert importlib.util.find_spec('iic_copilot') is None
assert 'site-packages' in str(DATA_DIR)
assert (UI_DIR / 'index.html').is_file()
assert (DATA_DIR / 'corpus/cstat_moc7_anexo_i.json').is_file()
package = SchemaPackage.load()
result = analyze_fiscal_xml(sys.stdin.read().encode(), package)
assert result['xsd_validation'] == 'valid', result
from importlib.metadata import distribution
metadata = json.loads(distribution('copilot-core').read_text('direct_url.json'))
assert not metadata.get('dir_info', {}).get('editable', False)
print('Wheel DF-e: UI, catálogo e XSD válidos; core não editável; sem shell IIC')
"""
            fixture = ROOT / "packages/dfe/tests/fixtures/nfe_controle_positivo_sp.xml"
            print(docker("exec", "-i", container, "python", "-c", code, data=fixture.read_text()))
            with request("/auth/logout", {}) as response:
                assert json.load(response)["ok"]
            with request("/auth/session") as response:
                assert json.load(response)["authenticated"] is False
            print(
                "Smoke HTTP: health, login, cookie, catálogo fiscal, UI/assets, validação e logout aprovados"
            )
        finally:
            if compose_args:
                docker(*compose_args, "down", "--volumes")
            elif container:
                subprocess.run(
                    ["docker", "rm", "--force", container], capture_output=True, check=False
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--compose", action="store_true")
    args = parser.parse_args()
    main(args.image, args.compose)
