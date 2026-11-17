from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_kyma_desliga_o_fallback_da_reference_library():
    """M-24: acervo de terceiros nao alimenta respostas na demo publica."""
    import yaml

    cm = yaml.safe_load((REPO_ROOT / "deploy/kyma/configmap.yaml").read_text(encoding="utf-8"))
    assert cm["data"]["REFERENCE_LIBRARY_FALLBACK_ENABLED"] == "false"
