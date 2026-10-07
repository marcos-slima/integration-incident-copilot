"""Validacao 2026-10-07, Bloco 3: infraestrutura local (M-18)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((RAIZ / "docker-compose.yml").read_text(encoding="utf-8"))


def test_m18_toda_porta_publicada_so_em_loopback():
    for nome, servico in COMPOSE["services"].items():
        for porta in servico.get("ports", []):
            assert re.match(r"^(127\.0\.0\.1|\$\{REDIS_BIND_IP:-127\.0\.0\.1\}):", porta), (
                f"{nome}: {porta}"
            )


def test_m18_nenhuma_imagem_latest():
    for nome, servico in COMPOSE["services"].items():
        imagem = servico.get("image")
        if imagem:
            assert not imagem.endswith(":latest") and (":" in imagem or "@" in imagem), nome


def test_m18_portas_de_host_nao_colidem_entre_perfis():
    vistas = {}
    for nome, servico in COMPOSE["services"].items():
        for porta in servico.get("ports", []):
            host = porta.rsplit(":", 1)[0]
            host = re.sub(r"\$\{[A-Z_]+:-([^}]+)\}", r"\1", host)
            assert host not in vistas, f"{nome} e {vistas.get(host)} publicam {host}"
            vistas[host] = nome


def test_m18_worker_le_o_mesmo_env_da_api():
    assert "./.env:/app/.env:ro" in COMPOSE["services"]["api"]["volumes"]
    assert "./.env:/app/.env:ro" in COMPOSE["services"]["worker"]["volumes"]


def test_m18_start_docker_sem_ai_stack_e_sem_uv_no_container():
    script = (RAIZ / "scripts" / "start-docker.sh").read_text(encoding="utf-8")
    codigo = "\n".join(linha for linha in script.splitlines() if not linha.lstrip().startswith("#"))
    assert "ai-stack" not in codigo and "AI_STACK_DIR" not in codigo
    assert "uv run" not in codigo
    # barra invertida seguida de linha de comentario quebrava o `docker compose run`
    assert not re.search(r"\\\n\s*#", script)


@pytest.mark.skipif(shutil.which("bash") is None, reason="sem bash")
def test_m18_start_docker_sintaxe():
    subprocess.run(["bash", "-n", str(RAIZ / "scripts" / "start-docker.sh")], check=True)


def test_embedding_backend_vem_do_env_file(tmp_path, monkeypatch):
    """EMBEDDING_BACKEND no .env era ignorado (lido so de os.environ no import
    do retriever); container e `uv run` caiam no Ollama sem aviso."""
    from app.config import Settings

    monkeypatch.delenv("EMBEDDING_BACKEND", raising=False)
    env = tmp_path / ".env"
    env.write_text("EMBEDDING_BACKEND=fastembed\n", encoding="utf-8")
    assert Settings(_env_file=env).embedding_backend == "fastembed"
