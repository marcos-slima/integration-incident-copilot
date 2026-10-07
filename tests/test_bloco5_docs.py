"""Bloco 5 (validacao 2026-10-07): integridade da documentacao.

- docs_env_vars: variavel de ambiente citada na doc tem de ser lida pelo
  projeto (CONNECTORS.md antigo deixava o conector em modo demo sem aviso).
- docs_code_references: intervalo `arquivo.py:10-20` passa a ser conferido.
- os documentos removidos (UC_*, auditorias, TESTING_E2E) nao voltam.
"""

from __future__ import annotations

from pathlib import Path

from app.evaluation.gates import (
    REPO_ROOT,
    check_docs_code_references,
    check_docs_env_vars,
    known_env_vars,
)


def _root(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "app").mkdir()
    (tmp_path / "app/config.py").write_text(
        "class Settings:\n    odata_service_url: str = ''\n", encoding="utf-8"
    )
    return tmp_path


def _falhas(findings):
    return [f for f in findings if f.is_failure]


def test_env_var_inexistente_em_bloco_reprova(tmp_path):
    root = _root(tmp_path)
    (root / "docs/G.md").write_text("```bash\nODATA_BASE_URL=https://x\n```\n", encoding="utf-8")
    falhas = _falhas(check_docs_env_vars(root))
    assert falhas and "ODATA_BASE_URL" in falhas[0].message


def test_env_var_inexistente_em_backticks_reprova(tmp_path):
    root = _root(tmp_path)
    (root / "docs/G.md").write_text("Configure `OAUTH2_CLIENT_ID`.\n", encoding="utf-8")
    assert "OAUTH2_CLIENT_ID" in _falhas(check_docs_env_vars(root))[0].message


def test_env_var_do_settings_passa(tmp_path):
    root = _root(tmp_path)
    (root / "docs/G.md").write_text(
        "```bash\nODATA_SERVICE_URL=https://x\n```\nUse `ODATA_SERVICE_URL`.\n", encoding="utf-8"
    )
    assert not _falhas(check_docs_env_vars(root))


def test_nome_abap_e_prosa_sem_backtick_nao_sao_variaveis(tmp_path):
    root = _root(tmp_path)
    (root / "docs/G.md").write_text(
        "Chama `RFC_SYSTEM_INFO`. A flag antiga USE_GRAPH_RAG nao existe.\n", encoding="utf-8"
    )
    assert not _falhas(check_docs_env_vars(root))


def test_variavel_do_compose_conta_como_conhecida(tmp_path):
    root = _root(tmp_path)
    (root / "docker-compose.yml").write_text(
        "services:\n  pg:\n    environment:\n      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}\n",
        encoding="utf-8",
    )
    assert "POSTGRES_PASSWORD" in known_env_vars(root)


def test_repo_real_conhece_as_variaveis_dos_conectores():
    conhecidas = known_env_vars(REPO_ROOT)
    for nome in ("ODATA_SERVICE_URL", "SAP_ASHOST", "SFSF_BASE_URL", "PO_BASE_URL"):
        assert nome in conhecidas
    for nome in ("ODATA_BASE_URL", "RFC_USER", "SERVICENOW_INSTANCE", "CAP_BASE_URL"):
        assert nome not in conhecidas


def test_intervalo_de_linhas_e_conferido(tmp_path):
    root = _root(tmp_path)
    (root / "app/x.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    (root / "docs/T.md").write_text("veja `app/x.py:1-9`\n", encoding="utf-8")
    falhas = _falhas(check_docs_code_references(root))
    assert falhas and "cita a 9" in falhas[0].message
    (root / "docs/T.md").write_text("veja `app/x.py:1-2`\n", encoding="utf-8")
    assert not _falhas(check_docs_code_references(root))


def test_documentos_ficticios_nao_voltam():
    docs = REPO_ROOT / "docs"
    assert not list(docs.glob("UC_0*.md"))
    for nome in (
        "AUDITORIA_PONTA_A_PONTA.md",
        "AUDITORIA_RESUMO_EXECUTIVO.md",
        "TESTING_E2E_USER_FLOW.md",
    ):
        assert not (docs / nome).exists()
    assert (docs / "CASOS_DE_USO.md").is_file()


def test_kyma_desliga_o_fallback_da_reference_library():
    """M-24: acervo de terceiros nao alimenta respostas na demo publica."""
    import yaml

    cm = yaml.safe_load((REPO_ROOT / "deploy/kyma/configmap.yaml").read_text(encoding="utf-8"))
    assert cm["data"]["REFERENCE_LIBRARY_FALLBACK_ENABLED"] == "false"
