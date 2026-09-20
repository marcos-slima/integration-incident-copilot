"""DA-24: validacao estrutural (nao contra cluster real - ver
deploy/kyma/README.md para as limitacoes explicitas desta fase) dos
manifests Kyma - garante que YAML permanece sintaticamente valido e
internamente consistente (namespace, referencias entre arquivos) a
medida que o projeto evolui, sem exigir kubectl/kyma CLI neste
ambiente de desenvolvimento."""

from pathlib import Path

import yaml

KYMA_DIR = Path(__file__).resolve().parent.parent / "deploy" / "kyma"
NAMESPACE = "sap-integration-copilot"


def _load(filename: str) -> dict:
    with open(KYMA_DIR / filename, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_all_yaml_files_are_syntactically_valid():
    yaml_files = sorted(KYMA_DIR.glob("*.yaml"))
    assert len(yaml_files) >= 8, "esperado pelo menos os 8 manifests do bundle DA-24"
    for path in yaml_files:
        with open(path, encoding="utf-8") as f:
            docs = list(yaml.safe_load_all(f))
        assert docs and all(doc for doc in docs), f"{path.name} nao produziu um doc YAML valido"


def test_namespaced_resources_use_the_same_namespace():
    for filename in (
        "configmap.yaml",
        "deployment.yaml",
        "service.yaml",
        "hpa.yaml",
        "apirule.yaml",
        "secret.example.yaml",
    ):
        doc = _load(filename)
        assert doc["metadata"]["namespace"] == NAMESPACE, filename


def test_namespace_manifest_creates_the_expected_namespace():
    doc = _load("namespace.yaml")
    assert doc["kind"] == "Namespace"
    assert doc["metadata"]["name"] == NAMESPACE


def test_deployment_exposes_health_probes_without_auth_dependency():
    """Guardrail de design (ver deployment.yaml): os probes usam
    /health, nunca /diagnose ou outro endpoint autenticado - caso
    contrario um rollout ficaria preso em CrashLoopBackOff so porque a
    Secret de API_KEY nao bateu com o probe."""
    doc = _load("deployment.yaml")
    container = doc["spec"]["template"]["spec"]["containers"][0]
    for probe_name in ("readinessProbe", "livenessProbe"):
        assert container[probe_name]["httpGet"]["path"] == "/health"


def test_deployment_runs_as_non_root():
    doc = _load("deployment.yaml")
    pod_security_context = doc["spec"]["template"]["spec"]["securityContext"]
    assert pod_security_context["runAsNonRoot"] is True
    assert pod_security_context["runAsUser"] != 0


def test_deployment_env_comes_from_configmap_and_secret():
    doc = _load("deployment.yaml")
    container = doc["spec"]["template"]["spec"]["containers"][0]
    env_from_names = {
        ref_type: ref["name"] for entry in container["envFrom"] for ref_type, ref in entry.items()
    }
    assert env_from_names["configMapRef"] == "sap-integration-copilot-config"
    assert env_from_names["secretRef"] == "sap-integration-copilot-secrets"


def test_service_targets_the_deployment_container_port():
    deployment = _load("deployment.yaml")
    service = _load("service.yaml")
    container_port_name = deployment["spec"]["template"]["spec"]["containers"][0]["ports"][0][
        "name"
    ]
    assert service["spec"]["ports"][0]["targetPort"] == container_port_name


def test_hpa_targets_the_same_deployment():
    doc = _load("hpa.yaml")
    ref = doc["spec"]["scaleTargetRef"]
    assert ref["kind"] == "Deployment"
    assert ref["name"] == "sap-integration-copilot"
    assert doc["spec"]["minReplicas"] <= doc["spec"]["maxReplicas"]


def test_apirule_targets_the_same_service():
    doc = _load("apirule.yaml")
    assert doc["spec"]["service"]["name"] == "sap-integration-copilot"


def test_secret_example_has_no_real_looking_values():
    """Todo valor do template deve comecar com o marcador CHANGE-ME -
    protege contra alguem commitar um segredo real por engano dentro
    do proprio arquivo de exemplo."""
    doc = _load("secret.example.yaml")
    for key, value in doc["stringData"].items():
        assert value.startswith("CHANGE-ME"), f"{key} nao parece um placeholder"


def test_kustomization_only_references_existing_files():
    doc = _load("kustomization.yaml")
    for resource in doc["resources"]:
        assert (KYMA_DIR / resource).exists(), resource
    # secret.example.yaml e deliberadamente um template, nao um
    # recurso do kustomization - ver README.md e o comentario no
    # proprio kustomization.yaml.
    assert "secret.example.yaml" not in doc["resources"]
