# Deploy no SAP BTP Kyma Runtime (DA-24)

Manifests reais para rodar o SAP Integration Copilot num cluster Kyma
(SAP BTP). Fecha o último item do roadmap arquitetural consolidado
deste projeto (ver `docs/DECISOES_DE_ARQUITETURA.md`, seção 22).

## O que tem aqui

| Arquivo | O que faz |
|---|---|
| `namespace.yaml` | Namespace dedicado `integration-incident-copilot` |
| `configmap.yaml` | Configuração não sensível (provider de LLM, URL do Qdrant, flags) |
| `secret.example.yaml` | **Template** de segredos (API keys, credenciais de LLM) - nunca aplicar direto, copiar e preencher |
| `deployment.yaml` | Deployment da API (2 réplicas, probes em `/health`, usuário não-root, requests/limits) |
| `service.yaml` | Service `ClusterIP` expondo a porta 80 → 8000 |
| `hpa.yaml` | HorizontalPodAutoscaler (2-6 réplicas, 70% CPU) |
| `apirule.yaml` | APIRule do módulo API Gateway do Kyma - expõe o Service via o Istio Gateway gerenciado |
| `kustomization.yaml` | Amarra os manifests acima para `kubectl apply -k` |

## Como aplicar

```bash
# 1. Build e push da imagem (registry acessível pelo cluster Kyma)
docker build -t <REGISTRY>/integration-incident-copilot:<TAG> .
docker push <REGISTRY>/integration-incident-copilot:<TAG>

# 2. Ajuste deployment.yaml com a imagem real (troque <REGISTRY>/<TAG>)
#    e apirule.yaml com o dominio real do cluster (troque <CLUSTER_DOMAIN>)

# 3. Segredos - NUNCA commitar preenchido, ver secret.example.yaml
cp secret.example.yaml /tmp/secret.yaml
# edite /tmp/secret.yaml com os valores reais
kubectl apply -f /tmp/secret.yaml
rm /tmp/secret.yaml

# 4. Todo o resto (namespace, config, deployment, service, hpa, apirule)
kubectl apply -k .

# 5. Acompanhe o rollout
kubectl -n integration-incident-copilot rollout status deployment/integration-incident-copilot
kubectl -n integration-incident-copilot get apirule integration-incident-copilot
```

## Pré-requisitos não cobertos por este bundle

- **Qdrant**: `configmap.yaml` aponta para `http://qdrant:6333` dentro
  do mesmo namespace, mas este bundle NÃO implanta o Qdrant em si -
  use o [Helm chart oficial da Qdrant](https://github.com/qdrant/qdrant-helm)
  ou um serviço gerenciado. Sem isso, a liveness (`/health`) passa, mas a
  readiness (`/ready`) fica em 503 e o Pod não recebe tráfego.
- **Redis**: `REDIS_URL` vem do Secret e aponta para `redis:6379`, que este
  bundle também não implanta.

O diagrama de implantação, com a rota real do LLM nesta configuração, está
em [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md#implantacao).
- **GraphRAG (Neo4j)**: continua opt-in (`GRAPH_RAG_ENABLED=false` no
  ConfigMap, mesma decisão de arquitetura #9 do README) - ligar em
  Kyma exigiria implantar um Neo4j separadamente, fora deste bundle.
- **Indexação da base de conhecimento**: rodar o script de indexação
  do Qdrant (ver seção 6 de `docs/DEPLOY.md`) contra o Qdrant do
  cluster, uma vez, após ele estar de pé.

## Validação em Kubernetes real (2026-10-18)

Este bundle foi aplicado sem cópia num k3s local pelo overlay
[`../local/`](../local/README.md) (`scripts/k3d-validate.sh`): rollout de API,
worker, Qdrant e Redis, probes, ingest no Pod, `/diagnose` pelo rule engine e
pelo LLM local, HPA escalando. A imagem foi construída de fato. Evidência em
`docs/EVIDENCIA_K3D_2026-10-18.md`; reprodução em
`docs/TUTORIAL_K3D_KUBERNETES_LOCAL.md`.

## Limitações explícitas (DA-24)

O que segue sem validação é o específico do Kyma gerenciado: nenhum destes
manifests foi aplicado num cluster **Kyma** (o trial exige onboarding
controlado pela SAP). Em particular:

- O schema do CRD `APIRule` já mudou de versão mais de uma vez na
  história do Kyma - o `apiVersion: gateway.kyma-project.io/v1beta1`
  usado aqui é o correto até o conhecimento deste projeto, mas **deve
  ser conferido contra o cluster alvo** (`kubectl explain apirule.spec`)
  antes de aplicar de verdade.
- O push para um registry acessível pelo Kyma não foi testado (no k3d a
  imagem é importada direto no nó, sem registry).
- `accessStrategy: noop` no `APIRule` assume que a autenticação por
  API key de cada endpoint (DA-18/DA-23) é suficiente. Trocar para
  `jwt` (validando tokens XSUAA do BTP) seria a evolução natural se
  este projeto avançar para integração mais profunda com serviços BTP
  (Destination service, XSUAA) - escopo explicitamente descartado
  nesta fase em favor de só empacotar o deploy.

## Conteúdo de terceiros na demo pública (M-24)

O `configmap.yaml` traz `REFERENCE_LIBRARY_FALLBACK_ENABLED: "false"`. A
collection `sap_reference_library` é montada a partir de livros e manuais de
terceiros, sem licença de redistribuição. Como fallback do RAG (DA-17), os
trechos dela entrariam no prompt, no relatório e na resposta da API, o que
numa demo pública equivale a redistribuí-los. Ligue o fallback só com
conteúdo próprio ou licenciado.

## Recursos Kubernetes/Kyma

O **Dockerfile** gerou uma imagem de ~512MB (sem CUDA, CPU-only), mas o consumo de memória em runtime é maior:

| Componente | Memória Residente |
|---|---|
| FastAPI (app.main) | ~350MB |
| CrossEncoder (reranker) | ~420MB |
| Overhead (Python/GIL/Qdrant client) | ~200MB |
| **Total** | **~970MB** |

**Deployment Kyma (deploy/kyma/deployment.yaml:74-80):**
```yaml
resources:
  requests:
    cpu: "100m"
    memory: "512Mi"
  limits:
    cpu: "1000m"
    memory: "1024Mi"
```

**Motivo:** limites anteriores de `512Mi` causavam `OOMKill`. O valor de `1024Mi` garante estabilidade com carga normal.
