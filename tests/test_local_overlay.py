"""Cenario G: o overlay deploy/local reaproveita deploy/kyma sem copia e
so muda o que e especifico do Kyma ou do laboratorio. Validacao estrutural
(sem kubectl/kustomize); a validacao em cluster e scripts/k3d-validate.sh.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / "deploy" / "local"
KYMA = ROOT / "deploy" / "kyma"


def _load_all(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]


def _kustomization() -> dict:
    return _load_all(LOCAL / "kustomization.yaml")[0]


def test_overlay_uses_kyma_bundle_as_base():
    k = _kustomization()
    assert "../kyma" in k["resources"]
    assert k["namespace"] == _load_all(KYMA / "namespace.yaml")[0]["metadata"]["name"]


def test_apirule_delete_patch_targets_the_real_apirule():
    apirule = _load_all(KYMA / "apirule.yaml")[0]
    deletes = [
        yaml.safe_load(p["patch"])
        for p in _kustomization()["patches"]
        if "patch" in p and "$patch: delete" in p["patch"]
    ]
    assert len(deletes) == 1
    d = deletes[0]
    assert (d["apiVersion"], d["kind"], d["metadata"]["name"]) == (
        apirule["apiVersion"],
        apirule["kind"],
        apirule["metadata"]["name"],
    )


def test_json_patches_target_existing_resources():
    names = {
        (doc["kind"], doc["metadata"]["name"])
        for f in KYMA.glob("*.yaml")
        if f.name != "kustomization.yaml"
        for doc in _load_all(f)
    }
    for p in _kustomization()["patches"]:
        if "target" in p:
            assert (p["target"]["kind"], p["target"]["name"]) in names, p["target"]


def test_local_config_keeps_data_on_the_machine():
    from app.llm.origins import is_loopback_origin

    data = _load_all(LOCAL / "configmap-local.yaml")[0]["data"]
    assert data["LLM_PROVIDER"] == "ollama"
    assert data["LLM_FALLBACK_PROVIDER"] == ""
    assert data["DATA_SOVEREIGNTY_MODE"] == "strict"
    assert data["CONFIDENTIAL_ALLOWED_ORIGINS"] == ""
    assert data["WEB_SEARCH_ENABLED"] == "false"
    assert is_loopback_origin(data["OLLAMA_HOST"])


def test_local_config_keys_exist_in_settings():
    from app.config import Settings

    fields = {name.upper() for name in Settings.model_fields}
    data = _load_all(LOCAL / "configmap-local.yaml")[0]["data"]
    assert set(data) <= fields, set(data) - fields


def test_support_services_match_configured_urls():
    kyma_cfg = _load_all(KYMA / "configmap.yaml")[0]["data"]
    services = {
        doc["metadata"]["name"]: doc
        for f in ("qdrant-service.yaml", "redis-service.yaml")
        for doc in _load_all(LOCAL / f)
        if doc["kind"] == "Service"
    }
    assert kyma_cfg["QDRANT_URL"] == "http://qdrant:6333"
    assert services["qdrant"]["spec"]["ports"][0]["port"] == 6333
    assert services["redis"]["spec"]["ports"][0]["port"] == 6379


def test_qdrant_version_matches_compose():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    local = next(
        d for d in _load_all(LOCAL / "qdrant-deployment.yaml") if d["kind"] == "Deployment"
    )
    image = local["spec"]["template"]["spec"]["containers"][0]["image"]
    assert image == compose["services"]["qdrant"]["image"]


def test_validation_script_is_executable_and_checks_apirule_removal():
    script = ROOT / "scripts" / "k3d-validate.sh"
    assert script.stat().st_mode & 0o111
    text = script.read_text(encoding="utf-8")
    assert "kubectl apply -k" in text and "deploy/local" in text
    assert "APIRule nao foi removido" in text


def test_dockerfile_makes_data_dir_writable_for_appuser():
    """Regressao (validacao k3d): o ingest grava estado e manifesto em
    /app/data; criado pelo COPY, o diretorio ficava com dono root."""
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    mkdir = text.index("install -d -o appuser -g appuser /app/data")
    first_copy = text.index("COPY --chown=appuser:appuser data/")
    assert mkdir < first_copy
