"""DA-24: validacao estrutural (nao contra cluster real - ver
deploy/kyma/README.md para as limitacoes explicitas desta fase) dos
manifests Kyma - garante que YAML permanece sintaticamente valido e
internamente consistente (namespace, referencias entre arquivos) a
medida que o projeto evolui, sem exigir kubectl/kyma CLI neste
ambiente de desenvolvimento."""

from pathlib import Path

import yaml

KYMA_DIR = Path(__file__).resolve().parent.parent / "deploy" / "kyma"
NAMESPACE = "integration-incident-copilot"


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
    """Guardrail de design (ver deployment.yaml): liveness usa /health e
    readiness usa /ready (§4.3), nunca /diagnose ou outro endpoint autenticado - caso
    contrario um rollout ficaria preso em CrashLoopBackOff so porque a
    Secret de API_KEY nao bateu com o probe."""
    doc = _load("deployment.yaml")
    container = doc["spec"]["template"]["spec"]["containers"][0]
    assert container["livenessProbe"]["httpGet"]["path"] == "/health"
    assert container["readinessProbe"]["httpGet"]["path"] == "/ready"


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
    assert env_from_names["configMapRef"] == "integration-incident-copilot-config"
    assert env_from_names["secretRef"] == "integration-incident-copilot-secrets"


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
    assert ref["name"] == "integration-incident-copilot"
    assert doc["spec"]["minReplicas"] <= doc["spec"]["maxReplicas"]


def test_apirule_targets_the_same_service():
    doc = _load("apirule.yaml")
    assert doc["spec"]["service"]["name"] == "integration-incident-copilot"


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


def test_worker_consumes_diagnosis_queue_with_shared_config():
    """B-02: com REDIS_URL, eventos vao para a fila RQ "diagnosis" - sem
    um worker no cluster eles nunca seriam processados."""
    doc = _load("worker.yaml")
    container = doc["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["/app/.venv/bin/rq"]
    assert container["args"] == ["worker", "--url", "$(REDIS_URL)", "diagnosis"]
    refs = {next(iter(ref.values()))["name"] for ref in container["envFrom"]}
    assert refs == {"integration-incident-copilot-config", "integration-incident-copilot-secrets"}
    assert doc["spec"]["template"]["spec"]["securityContext"]["runAsNonRoot"] is True


def test_configmap_sem_referencia_dollar_parenteses():
    """Validacao 2026-10-06 (N-08): o kubelet so expande $(VAR) em
    env[].value, command e args. Em valor de ConfigMap lido via envFrom o
    processo recebe o texto literal "$(REDIS_PASSWORD)"."""
    doc = _load("configmap.yaml")
    literais = {k: v for k, v in doc["data"].items() if "$(" in str(v)}
    assert literais == {}, f"valores nao seriam expandidos: {sorted(literais)}"
    assert "REDIS_URL" not in doc["data"] and "NEO4J_PASSWORD" not in doc["data"]


# ---------------------------------------------------------------------------
# Validacao 2026-10-07 (DEP-01)
# ---------------------------------------------------------------------------


def _mebibytes(value: str) -> int:
    if value.endswith("Gi"):
        return int(float(value[:-2]) * 1024)
    if value.endswith("Mi"):
        return int(value[:-2])
    raise AssertionError(f"unidade inesperada: {value}")


def test_api_e_worker_tem_memoria_para_o_reranker():
    """Medido com o venv da imagem: ~1156 MB residentes com o reranker
    carregado. API e worker rodam o mesmo grafo; limite < 1536Mi e OOMKill."""
    for filename in ("deployment.yaml", "worker.yaml"):
        container = _load(filename)["spec"]["template"]["spec"]["containers"][0]
        resources = container["resources"]
        assert _mebibytes(resources["requests"]["memory"]) >= 1536, filename
        assert _mebibytes(resources["limits"]["memory"]) >= 2048, filename


def test_apirule_libera_os_metodos_do_painel_admin():
    methods = set(_load("apirule.yaml")["spec"]["rules"][0]["methods"])
    assert {"GET", "POST", "PUT", "PATCH", "DELETE"} <= methods


def test_configmap_de_producao():
    data = _load("configmap.yaml")["data"]
    # sem Ollama no cluster: embeddings in-process
    assert data["EMBEDDING_BACKEND"] == "fastembed"
    assert data["EXPOSE_API_DOCS"] == "false"
    assert data["SESSION_COOKIE_SECURE"] == "true"
    trusted = data["FORWARDED_ALLOW_IPS"]
    assert "*" not in trusted.split(","), "'*' faz o uvicorn confiar no XFF do cliente"


def test_dockerfile_sem_toolchain_no_runtime_e_com_migrations():
    dockerfile = (KYMA_DIR.parent.parent / "Dockerfile").read_text(encoding="utf-8")
    runtime = dockerfile.split("FROM python:3.12-slim AS app", 1)[1]
    for pacote in ("gcc", "cmake", "libssl-dev", "libsasl2-dev"):
        assert pacote not in runtime.split("FROM app AS final", 1)[0], pacote
    assert "COPY --chown=appuser:appuser alembic/ alembic/" in runtime
    assert "--extra openai" in dockerfile
    assert "--proxy-headers" in dockerfile
    assert "pkg-config" in dockerfile.split("AS builder", 1)[1].split("AS app", 1)[0]
    assert "SSL.present()" in dockerfile
    assert "FASTEMBED_BM25_PATH=/app/.cache/bm25" in runtime
    assert '"--forwarded-allow-ips", "*"' not in dockerfile
