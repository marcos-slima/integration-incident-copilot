"""Exercise the installed IIC image without live diagnosis or external services."""

import json
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


with tempfile.TemporaryDirectory(prefix="iic-smoke-") as tmp:
    key = secrets.token_urlsafe(32)
    env = {
        name: secrets.token_urlsafe(32)
        for name in ["A2A_API_KEY", "ADMIN_API_KEY", "EVENT_MESH_API_KEY", "SESSION_SECRET"]
    }
    env.update(
        API_KEY=key,
        REQUIRE_AUTH="true",
        DATABASE_URL="",
        REDIS_URL="",
        GRAPH_RAG_ENABLED="false",
        AMQP_ENABLED="false",
        QDRANT_URL="http://127.0.0.1:9",
        OLLAMA_HOST="http://127.0.0.1:9",
    )
    path = Path(tmp) / "runtime.env"
    path.write_text("\n".join(f"{k}={v}" for k, v in env.items()))
    path.chmod(0o600)
    container = docker(
        "run",
        "--rm",
        "--detach",
        "--env-file",
        str(path),
        "--publish",
        "127.0.0.1::8000",
        sys.argv[1],
    )
    try:
        base = "http://127.0.0.1:" + docker("port", container, "8000/tcp").rsplit(":", 1)[1]
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request(route, body=None, auth=False):
            headers = {"Content-Type": "application/json"}
            if auth:
                headers["X-API-Key"] = key
            return client.open(
                urllib.request.Request(
                    base + route, data=json.dumps(body).encode() if body else None, headers=headers
                ),
                timeout=3,
            )

        deadline = time.monotonic() + 90
        while True:
            try:
                with request("/health") as response:
                    assert json.load(response) == {"status": "ok"}
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        with request("/") as response:
            html = response.read().decode()
        assets = re.findall(r'(?:src|href)="([^\"]+\.(?:js|css))"', html)
        assert assets
        for asset in assets:
            with request(asset) as response:
                assert response.status == 200
        for auth, expected in [(False, 401), (True, 422)]:
            try:
                request(
                    "/diagnose",
                    {"description": "contract probe", "interface_type": "unsupported"},
                    auth,
                )
                raise AssertionError("Invalid request accepted")
            except urllib.error.HTTPError as error:
                assert error.code == expected
        code = "import importlib.util,os,pathlib; from importlib.metadata import version; from proton import SSL; import iic_copilot; assert os.getuid()!=0; assert importlib.util.find_spec('dfe_copilot') is None; assert '+cpu' in version('torch'); assert SSL.present(); assert not pathlib.Path('/app/app').exists(); assert pathlib.Path('/app/alembic/versions').is_dir(); print(version('copilot-core'))"
        print("Installed IIC image verified; core", docker("exec", container, "python", "-c", code))
    finally:
        docker("stop", container)
