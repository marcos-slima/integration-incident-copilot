# Deploy no SAP BTP Kyma Runtime (DA-24)

Manifests reais para rodar o SAP Integration Copilot num cluster Kyma
(SAP BTP). Fecha o último item do roadmap arquitetural consolidado
deste projeto (ver `README.md` na raiz, Decisão de Arquitetura ### 22).

## O que tem aqui

| Arquivo | O que faz |
|---|---|
| `namespace.yaml` | Namespace dedicado `sap-integration-copilot` |
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
docker build -t <REGISTRY>/sap-integration-copilot:<TAG> .
docker push <REGISTRY>/sap-integration-copilot:<TAG>

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
kubectl -n sap-integration-copilot rollout status deployment/sap-integration-copilot
kubectl -n sap-integration-copilot get apirule sap-integration-copilot
```

## Pré-requisitos não cobertos por este bundle

- **Qdrant**: `configmap.yaml` aponta para `http://qdrant:6333` dentro
  do mesmo namespace, mas este bundle NÃO implanta o Qdrant em si -
  use o [Helm chart oficial da Qdrant](https://github.com/qdrant/qdrant-helm)
  ou um serviço gerenciado. Sem isso, o Pod sobe (probes de `/health`
  passam), mas `/diagnose` falha ao tentar consultar o RAG.
- **GraphRAG (Neo4j)**: continua opt-in (`GRAPH_RAG_ENABLED=false` no
  ConfigMap, mesma decisão de arquitetura #9 do README) - ligar em
  Kyma exigiria implantar um Neo4j separadamente, fora deste bundle.
- **Indexação da base de conhecimento**: rodar o script de indexação
  do Qdrant (ver seção 6 de `docs/DEPLOY.md`) contra o Qdrant do
  cluster, uma vez, após ele estar de pé.

## Limitações explícitas desta fase (DA-24)

Nenhum destes manifests foi validado contra um cluster Kyma real -
não há cluster acessível neste ambiente de desenvolvimento, mesma
limitação já registrada para Neo4j (DA-21) e para testes reais de MCP
contra infraestrutura externa (DA-19). Em particular:

- O schema do CRD `APIRule` já mudou de versão mais de uma vez na
  história do Kyma - o `apiVersion: gateway.kyma-project.io/v1beta1`
  usado aqui é o correto até o conhecimento deste projeto, mas **deve
  ser conferido contra o cluster alvo** (`kubectl explain apirule.spec`)
  antes de aplicar de verdade.
- Nenhum build/push de imagem Docker foi testado nesta fase (Docker
  não está disponível neste ambiente de desenvolvimento) - o
  `Dockerfile` foi revisado e corrigido (build reprodutível com
  `uv.lock` + `--frozen`, usuário não-root, `CMD` direto no venv), mas
  não foi construído de fato.
- `accessStrategy: noop` no `APIRule` assume que a autenticação por
  API key de cada endpoint (DA-18/DA-23) é suficiente. Trocar para
  `jwt` (validando tokens XSUAA do BTP) seria a evolução natural se
  este projeto avançar para integração mais profunda com serviços BTP
  (Destination service, XSUAA) - escopo explicitamente descartado
  nesta fase em favor de só empacotar o deploy.
