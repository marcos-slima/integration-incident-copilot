# Overlay local (cenário G: Kubernetes genérico)

Aplica o **mesmo** bundle de `deploy/kyma/` num Kubernetes local (k3d/k3s),
sem cópia dos manifests. É a forma de validar Deployment, worker, Service,
HPA, probes, ConfigMap e Secret num cluster real enquanto o Kyma gerenciado
não está disponível.

| O que muda em relação ao Kyma | Por quê |
|---|---|
| `APIRule` removido (`$patch: delete`) | É CRD do módulo api-gateway do Kyma; aqui o acesso é por `kubectl port-forward` |
| `qdrant-*.yaml` e `redis-*.yaml` incluídos | O bundle Kyma declara que não os implanta |
| `configmap-local.yaml`: Ollama no host, `DATA_SOVEREIGNTY_MODE=strict`, busca web desligada | Nada sai da máquina |
| Imagem `integration-incident-copilot:local`, `imagePullPolicy: Never` | Importada no k3d, sem registry |
| 1 réplica, HPA 1–2 | Cluster de um nó |

## Executar

```bash
scripts/k3d-validate.sh            # cria cluster, build, aplica, indexa, testa
scripts/k3d-validate.sh --skip-build
scripts/k3d-validate.sh --delete   # remove o cluster
```

O script grava a evidência em `reports/k3d-validation-<timestamp>.log` e o
YAML renderizado em `reports/k3d-rendered-<timestamp>.yaml`.

**Pré-requisitos:** `docker`, `k3d` (v5), `kubectl`, `curl`, `python3` e o
Ollama no host ouvindo numa interface que containers alcancem (o mesmo
requisito do `docker compose`, que usa `host.docker.internal`), com o modelo
de `configmap-local.yaml` baixado.

`host.docker.internal` é mapeado para o IP do host via `hostAliases` (o
script lê `host.k3d.internal` do CoreDNS do k3d). A policy de soberania
trata esse nome como loopback (`app/llm/origins.py::is_loopback_origin`).

## Limites

- Não valida o que é específico do Kyma: `APIRule`, Istio, OIDC do BTP.
- Qdrant e Redis sem persistência (`emptyDir` / `--save ""`).
- Sem Postgres: persistência e superfície admin ficam desligadas
  (`DATABASE_URL` vazio), como no modo sem banco do app.
