# DA_AULA_19_KYMA.md — Kyma & Deployment Constraints (Módulo 19)

## Objetivo

Documentar o deployment no SAP BTP Kyma Runtime (DA-24):
- Manifests Kyma (Deployment, Service, HPA, APIRule, ConfigMap, Secret)
- Restrições e limitações do ambiente Kyma
- Variáveis de ambiente por environment (dev vs. Kyma)
- Escalabilidade e observabilidade

---

## Arquitetura

### Manifests Kyma (DA-24)

O projeto fornece um conjunto completo de manifests em `deploy/kyma/`:

| Arquivo | Recurso Kubernetes | Responsabilidade |
|---|---|---|
| `namespace.yaml` | `Namespace` | `sap-integration-copilot` |
| `configmap.yaml` | `ConfigMap` | Variáveis não-sensíveis (LLM_PROVIDER, QDRANT_URL, etc.) |
| `secret.example.yaml` | `Secret` (template) | Credenciais (API_KEY, OPENAI_API_KEY, etc.) |
| `deployment.yaml` | `Deployment` | Pod(s) com API (DA-24: não-root user, liveness/readiness probes) |
| `service.yaml` | `Service` | ClusterIP, porta 80 → 8000 |
| `hpa.yaml` | `HPA` | Escala 2-6 pods por CPU 70% |
| `apirule.yaml` | `APIRule` (Kyma) | Exposição via Istio Gateway (API Gateway do Kyma) |

**Importante:** Manifests são **exemplo** — não validados contra cluster real (mesma limitação que Neo4j/Docker nas DAs-21/19).

### Deployment (DA-24)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sap-integration-copilot
  namespace: sap-integration-copilot
spec:
  replicas: 2
  selector:
    matchLabels:
      app.kubernetes.io/name: sap-integration-copilot
  template:
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: api
          image: "<REGISTRY>/sap-integration-copilot:<TAG>"
          ports:
            - name: http
              containerPort: 8000
          readinessProbe:
            httpGet:
              path: /ready
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /health
              port: http
            initialDelaySeconds: 15
            periodSeconds: 20
            failureThreshold: 3
          resources:
            requests:
              cpu: "100m"
              memory: "256Mi"
            limits:
              cpu: "500m"
              memory: "512Mi"
```

**Correção DA-24:**
- não-root (`runAsUser: 1000`) → PodSecurityStandards `restricted`
- `readinessProbe` → `/ready` (infrastructure check), `livenessProbe` → `/health` (process alive)
- `resources` conservativos → workload I/O-bound (chamadas HTTP a LLM/Qdrant)

### Service e APIRule

**Service (ClusterIP):**
```yaml
apiVersion: v1
kind: Service
metadata:
  name: sap-integration-copilot
  namespace: sap-integration-copilot
spec:
  selector:
    app.kubernetes.io/name: sap-integration-copilot
  ports:
    - name: http
      port: 80
      targetPort: http  # containerPort: 8000
  type: ClusterIP
```

**APIRule (Kyma Gateway):**
```yaml
apiVersion: gateway.kyma-project.io/v1beta1
kind: APIRule
metadata:
  name: sap-integration-copilot
  namespace: sap-integration-copilot
spec:
  host: sap-integration-copilot.<CLUSTER_DOMAIN>
  service:
    name: sap-integration-copilot
    port: 80
  gateway: kyma-system/kyma-gateway
  rules:
    - path: /*
      methods: ["GET", "POST"]
      accessStrategies:
        - handler: noop
```

**Por `handler: noop`?** O Copilot já tem autenticação por API key (`X-API-Key`, DA-18), não há necessidade de camada extra.

### HPA (Escalabilidade Horizontal)

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: sap-integration-copilot
  namespace: sap-integration-copilot
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: sap-integration-copilot
  minReplicas: 2
  maxReplicas: 6
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

**Justificativa (DA-24):** Escalar replicas é mais barato que aumentar limits, pois workload é I/O-bound (LLM Gateway calls) mas consome CPU/memória por worker uvicorn.

### ConfigMap (variáveis não-sensíveis)

**Valores defaults:**
| Chave | Valor | Observação |
|---|---|---|
| `LLM_PROVIDER` | `openai` | Ollama local não prático em Pod Kyma (GBs, GPU) |
| `LLM_FALLBACK_PROVIDER` | `azure_openai` | DA-20: fallback para resiliência |
| `LLM_MODEL` | `gpt-4o-mini` | Modelo canônico em cloud |
| `QDRANT_URL` | `http://qdrant:6333` | Qdrant implantado no mesmo cluster (Helm chart) |
| `GRAPH_RAG_ENABLED` | `false` | opt-in (DA-9: manter stack default leve) |
| `REDIS_URL` | `redis://redis:6379/0` | Task store compartilhado entre replicas (HPA) |
| `WEB_SEARCH_ENABLED` | `true` | Sempre ligado em cloud (não depende de infra local) |
| `WEB_SEARCH_POLICY` | `public_only` | DA-02: busca web só quando `classify_sensitivity() == public` |
| `DATA_SOVEREIGNTY_MODE` | `cloud_with_dlp` | PII redaction pronto antes do gateway (DA-39) |
| `CONFIDENTIAL_ALLOWED_ORIGINS` | `https://<recurso>.openai.azure.com` | DA-43: allowlist de origens (não provider labels) |

**Template de redeção (DA-43):**
```yaml
# O campo "origin" e, por definicao, scheme://host[:port] (ex: https://my-resource.openai.azure.com).
# PROVIDER (openai/azure_openai) e apenas um rótulo: um provider pode apontar para api.openai.com,
# vLLM (http://localhost:8000), Groq (https://api.groq.com), LiteLLM (http://localhost:4000), etc.
# A unidade de governanca e a ORIGIN (URL completa), nao o rótulo (DA-43).
CONFIDENTIAL_ALLOWED_ORIGINS: "https://my-resource.openai.azure.com"
```

### Secret (credenciais)

**Template (`secret.example.yaml`)** — NUNCA commitar valores reais (gitleaks bloqueia):

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: sap-integration-copilot-secrets
  namespace: sap-integration-copilot
stringData:
  API_KEY: "CHANGE-ME-diagnose-api-key"
  A2A_API_KEY: "CHANGE-ME-a2a-api-key"
  EVENT_MESH_API_KEY: "CHANGE-ME-event-mesh-webhook-key"
  OPENAI_API_KEY: "CHANGE-ME-openai-key"
  AZURE_OPENAI_API_KEY: "CHANGE-ME-azure-openai-key"
  AZURE_OPENAI_ENDPOINT: "CHANGE-ME-https://<resource>.openai.azure.com"
  AZURE_OPENAI_DEPLOYMENT: "CHANGE-ME-deployment-name"
  LANGFUSE_PUBLIC_KEY: "CHANGE-ME-langfuse-public-key"
  LANGFUSE_SECRET_KEY: "CHANGE-ME-langfuse-secret-key"
```

**Recomendação:** Usar credential store externo (SAP Credential Store, HashiCorp Vault, External Secrets Operator) em vez de manifest estático.

---

## Restrições do Kyma

### DA-24: Limitações Conhecidas

1. **Ollama local não prático** → Imagem de vários GBs, exige GPU/CPU dedicada para latência aceitável
2. **Qdrant externo** → Deve ser implantado separadamente (Helm chart oficial ou serviço gerenciado)
3. **Redis para Statefulness** → necessário para task store compartilhado entre replicas (HPA define 2-6 pods)
4. **APIRule schema instável** → Schema do CRD `APIRule` mudou (`v1alpha1` → `v1beta1`), deve ser validado no cluster alvo
5. **Sem testes E2E em cluster real** → Manifests não validados (mesma limitação que Neo4j/Docker)

### Escalabilidade

- **HPA:** 2-6 replicas, escala por CPU 70%
- **Stateless API:** não há status no processo → pods podem ser reiniciados sem perda de dados
- **Statefulness externa:** Qdrant (vector DB), Postgres (incidentes), Redis (task store)

### Observabilidade

- **Langfuse opcional** → Sem chaves no ConfigMap, tracing é desativado automaticamente
- **Health endpoints distintos**
  - `/health` → liveness (processo vivo, sem IO externo)
  - `/ready` → readiness (Qdrant/Ollama/Redis acessíveis, B-07)

---

## Padrões

### Variáveis de Ambiente (Kyma vs Dev)

| Variável | Dev (Docker) | Kyma |
|---|---|---|
| `LLM_PROVIDER` | `ollama` (local) | `openai` (cloud) |
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | não usado |
| `QDRANT_URL` | `http://qdrant:6333` | `http://qdrant:6333` (mesmo) |
| `DATABASE_URL` | `postgresql+asyncpg://...@postgres:5432/...` | `postgresql+asyncpg://...@postgres:5432/...` (mesmo) |

### Ordem de Precedência

1. `ConfigMap` (definição base) → `Secret` (sobrescreve senhas) → `envFrom` (inject no container)
2. Variáveis undefined no ConfigMap/Secret → defaults do `app/config.py` (DA-43)

---

## Gates

| Gate | Origem | Verificação |
|---|---|---|
| `da_24_applied` | `app/evaluation/gates.py` | `deploy/kyma/deployment.yaml` tem `securityContext: runAsNonRoot: true`, `runAsUser: 1000` |
| `health_probes` | `app/evaluation/gates.py` | `/health` (liveness) e `/ready` (readiness) definidos no Deployment |
| `hpa_configured` | `app/evaluation/gates.py` | `deploy/kyma/hpa.yaml` existe com `minReplicas`, `maxReplicas`, `metrics` |
| `apirule_schema` | `app/evaluation/gates.py` | `apiVersion: gateway.kyma-project.io/v1beta1` (deve ser validado contra cluster real) |

---

## Exercícios

1. **Aplicar namespace:**

```bash
kubectl apply -f deploy/kyma/namespace.yaml
```

2. **Criar secrets (exemplo):**

```bash
cp deploy/kyma/secret.example.yaml /tmp/secret.yaml
# edite /tmp/secret.yaml com valores reais
kubectl apply -f /tmp/secret.yaml
rm /tmp/secret.yaml
```

3. **Aplicar ConfigMap:**

```bash
kubectl apply -f deploy/kyma/configmap.yaml
```

4. **Aplicar service + deployment:**

```bash
kubectl apply -f deploy/kyma/service.yaml
kubectl apply -f deploy/kyma/deployment.yaml
```

5. **Aplicar HPA + APIRule:**

```bash
kubectl apply -f deploy/kyma/hpa.yaml
kubectl apply -f deploy/kyma/apirule.yaml
```

6. **Verificar status:**

```bash
kubectl -n sap-integration-copilot get pods
kubectl -n sap-integration-copilot get hpa
kubectl -n sap-integration-copilot get_apirule
```

---

## Invariantes

1. **Não-root user** → `runAsUser: 1000`, `runAsNonRoot: true` (PodSecurityStandards `restricted`)
2. **Health probes distintos** → `/health` = liveness (processo vivo), `/ready` = readiness (infra acessível)
3. **Stateless API** → status em Qdrant/Postgres/Redis, não no processo
4. **APIRule não autentica** → `handler: noop`, autenticação já feita via `X-API-Key` (DA-18)

---

## Limitações

1. **Sem testes E2E em cluster real** → Manifests não validados (mesma limitação que Neo4j/Docker)
2. **APIRule schema instável** → Deve ser validado contra cluster alvo
3. **Qdrant externo** → Deve ser implantado separadamente
4. **Sem ingress controller nativo** → Usa APIRule (modulo API Gateway do Kyma), não Istio VirtualService/Gateway manual

---

## Referências

- DA-24: Deploy SAP BTP Kyma Runtime (build reproduzível, non-root user, CMD direto, healthcheck explícito)
- DA-26: AI Gateway (policy + circuit breaker + budget)
- DA-39: Soberania de dados no AI Gateway (`strict` / `cloud_with_dlp`)
- DA-43: Soberania de dados por ORIGIN real, fail-closed
- `deploy/kyma/*.yaml`: manifests Kyma
- `docs/DEPLOYMENT.md`: guia de deploy no Kyma (DA-24)
- `docs/DEPLOY.md`: passo a passo de deploy (DA-24)
- `scripts/deploy-kyma.sh`: script de deploy (não documentado, mas presente no repo)

---

## DAs relevantes

| DA | O que é | Onde |
|---|---|---|
| DA-24 | Deploy SAP BTP Kyma Runtime (build reproduzível, non-root user, CMD direto, healthcheck explícito) | `deploy/kyma/`, `Dockerfile`, `app/main.py::health()` |
| DA-26 | AI Gateway (policy + circuit breaker + budget) | `app/llm/gateway.py` |
| DA-39 | Soberania de dados no AI Gateway (`strict` / `cloud_with_dlp`) | `app/llm/gateway.py` |
| DA-43 | Soberania de dados por ORIGIN real, fail-closed | `app/llm/gateway.py`, `app/llm/origins.py` |
| DA-48 | Metering de tokens REAIS (real, não estimativa) | `app/admin/metering.py` |
