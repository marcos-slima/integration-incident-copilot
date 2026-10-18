# Tutorial: Kubernetes local com k3d — reproduzir a validação do deploy

> **Público-alvo:** usuários administrativos que precisam reproduzir, na
> própria máquina, a validação do deploy em Kubernetes (cenário G) — sem
> depender de um cluster SAP BTP Kyma.
>
> **O que se valida:** o bundle `deploy/kyma/` aplicado **sem cópia** num
> Kubernetes real (k3s via k3d), com API, worker, Qdrant e Redis, LLM local e
> soberania `strict`. Última execução registrada: 2026-10-18
> ([`EVIDENCIA_K3D_2026-10-18.md`](EVIDENCIA_K3D_2026-10-18.md)).
>
> **O que não se valida aqui:** o que é específico do Kyma gerenciado —
> `APIRule`, Istio e OIDC do BTP (DA-24).

---

## 1. Visão geral

```mermaid
%%{init: {'theme':'base','themeVariables':{'background':'#ffffff','primaryTextColor':'#1f2937','lineColor':'#64748b','fontSize':'13px'}}}%%
flowchart LR
  subgraph HOST["Máquina do administrador"]
    OL[Ollama<br/>:11434]
    DK[Docker]
  end
  subgraph K3D["Cluster k3d 'iic' (k3s)"]
    API[API<br/>Deployment+HPA] --> QD[(Qdrant)]
    API --> RD[(Redis)]
    WK[Worker RQ] --> RD
  end
  KY[deploy/kyma<br/>bundle] --> LO[deploy/local<br/>overlay] --> K3D
  API -. host.docker.internal .-> OL
  PF[port-forward<br/>:18000] --> API
  classDef h fill:#f1f5f9,stroke:#64748b,color:#1f2937
  classDef k fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
  classDef c fill:#fef3c7,stroke:#d97706,color:#78350f
  class OL,DK,PF h
  class API,WK,QD,RD k
  class KY,LO c
  style HOST fill:#ffffff,stroke:#94a3b8
  style K3D fill:#ffffff,stroke:#0284c7
```

| Peça | Onde | Papel |
|---|---|---|
| Bundle Kyma | `deploy/kyma/` | Namespace, ConfigMap, Deployment (API), worker, Service, HPA, APIRule |
| Overlay local | `deploy/local/` | Reaproveita o bundle: remove o `APIRule`, inclui Qdrant/Redis, LLM local, imagem local |
| Script | `scripts/k3d-validate.sh` | Cria o cluster, aplica, indexa, testa e grava a evidência |
| Teste estrutural | `tests/test_local_overlay.py` | Garante, no CI, que o overlay continua coerente com o bundle |

---

## 2. Pré-requisitos

| Item | Versão usada na validação | Como conferir |
|---|---|---|
| Docker | — | `docker info` |
| k3d | v5.9.0 | `k3d version` |
| kubectl | v1.35.9 | `kubectl version --client` |
| Ollama no host | ativo, com o modelo do overlay | `curl -s http://127.0.0.1:11434/api/tags` |
| Disco | ~15 GB livres (imagem ~6,7 GB + cópia no cluster) | `df -h` |

Instalação (Linux):

```bash
curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash
command -v kubectl || sudo snap install kubectl --classic
```

**Ollama acessível por containers.** O Pod chega ao Ollama do host pelo nome
`host.docker.internal`. O Ollama precisa ouvir numa interface que containers
alcancem — o mesmo requisito do `docker compose`. Se ele ouvir só em
`127.0.0.1`, o script avisa no passo 0. Para corrigir no serviço systemd:

```bash
sudo systemctl edit ollama     # adicione: [Service]  Environment="OLLAMA_HOST=0.0.0.0"
sudo systemctl restart ollama
```

O modelo usado é o de `deploy/local/configmap-local.yaml` (`LLM_MODEL`).
Confira se ele está baixado: `ollama list`.

---

## 3. Caminho rápido: o script

```bash
scripts/k3d-validate.sh               # 1ª vez: cria cluster, build, aplica, testa
scripts/k3d-validate.sh --skip-build  # reaproveita a imagem integration-incident-copilot:local
scripts/k3d-validate.sh --delete      # remove o cluster
```

Ao final:

```
OK: cenario G validado. Evidencia: .../reports/k3d-validation-<timestamp>.log
```

O que cada passo faz e por quê:

| Passo | O que acontece | Por que assim |
|---|---|---|
| 0 | Confere `docker`, `k3d`, `kubectl`, `curl` e o Ollama | Falha cedo, com a causa |
| 1 | Cria o cluster `iic` (1 nó, sem Traefik) se não existir | O acesso é por port-forward; o Ingress não é necessário |
| 2 | `docker build` e `k3d image import` | Sem registry: a imagem vai direto para o containerd do nó |
| 3 | Cria o Secret **uma vez por cluster**, com valores aleatórios | Regerar a cada execução trocava a senha do Redis só para os Pods novos |
| 4 | Renderiza (`kubectl kustomize`) e aplica `deploy/local` | O YAML renderizado vai para `reports/`; o script falha se o `APIRule` não tiver sido removido |
| 5 | Mapeia `host.docker.internal` para o IP do host (`hostAliases`) e faz `rollout restart` | A tag `:local` é fixa: sem restart, o Pod antigo seguiria com a imagem anterior |
| 6 | Espera o rollout dos 4 Deployments e registra imagem e dono de `/app/data` | Prova que o Pod roda a imagem nova, como `appuser` |
| 7 | Indexa `data/sample_docs` **dentro do Pod** | Exercita o mesmo caminho de ingest de um cluster real |
| 8 | Smoke test por port-forward (`:18000`) | `/health`, `/ready`, `401` sem chave, um diagnóstico pelo rule engine e outro pelo LLM |
| 9 | Estado do HPA e eventos | O k3s traz metrics-server; o HPA escala de fato |

Variáveis opcionais: `K3D_CLUSTER` (nome do cluster, default `iic`) e
`PF_PORT` (porta local do port-forward, default `18000`).

---

## 4. Passo a passo manual (para entender ou depurar)

Os mesmos passos do script, um por vez. Rode da raiz do repositório.

**4.1 Cluster**

```bash
k3d cluster create iic --agents 0 --wait --k3s-arg "--disable=traefik@server:0"
kubectl config use-context k3d-iic
kubectl get nodes          # STATUS Ready
```

**4.2 Imagem**

```bash
docker build -t integration-incident-copilot:local .
k3d image import integration-incident-copilot:local -c iic
```

**4.3 Namespace e Secret**

```bash
NS=integration-incident-copilot
kubectl apply -f deploy/kyma/namespace.yaml
R() { python3 -c 'import secrets; print(secrets.token_hex(24))'; }
RP=$(R)
kubectl -n $NS create secret generic integration-incident-copilot-secrets \
  --from-literal=API_KEY="$(R)" --from-literal=A2A_API_KEY="$(R)" \
  --from-literal=EVENT_MESH_API_KEY="$(R)" --from-literal=REDIS_PASSWORD="$RP" \
  --from-literal=REDIS_URL="redis://:$RP@redis:6379/0"
```

Os valores ficam só no cluster; nada é gravado em disco.

**4.4 Ver o que vai ser aplicado, depois aplicar**

```bash
kubectl kustomize deploy/local | grep -E '^kind:' | sort | uniq -c   # sem APIRule
kubectl apply -k deploy/local
```

Esperado: 1 ConfigMap, 4 Deployments, 1 HPA, 1 Namespace, 3 Services.

**4.5 Ollama do host visível no Pod**

```bash
HOST_IP=$(kubectl -n kube-system get cm coredns -o jsonpath='{.data.NodeHosts}' | awk '/host.k3d.internal/{print $1}')
for d in integration-incident-copilot integration-incident-copilot-worker; do
  kubectl -n $NS patch deployment $d --type=merge -p \
    "{\"spec\":{\"template\":{\"spec\":{\"hostAliases\":[{\"ip\":\"$HOST_IP\",\"hostnames\":[\"host.docker.internal\"]}]}}}}"
done
kubectl -n $NS rollout restart deployment/integration-incident-copilot deployment/integration-incident-copilot-worker
```

Por que `host.docker.internal` e não `host.k3d.internal`: a política de
soberania trata `host.docker.internal` como origem local
(`app/llm/origins.py::is_loopback_origin`). Com outro nome, o dado
confidencial seria bloqueado por ir a uma origem "remota".

**4.6 Rollout e verificação do Pod**

```bash
for d in qdrant redis integration-incident-copilot integration-incident-copilot-worker; do
  kubectl -n $NS rollout status deployment/$d --timeout=600s
done
kubectl -n $NS exec deploy/integration-incident-copilot -- sh -c 'id; ls -ld /app/data'
# uid=1000(appuser) ... e /app/data com dono appuser
```

**4.7 Indexação**

```bash
kubectl -n $NS exec deploy/integration-incident-copilot -- python -m app.rag.ingest --target incidents --reset-state
# [incidents] Manifesto do indice: 70 ponto(s) em sap_incident_docs
```

**4.8 Smoke test**

```bash
kubectl -n $NS port-forward svc/integration-incident-copilot 18000:80 &
KEY=$(kubectl -n $NS get secret integration-incident-copilot-secrets -o jsonpath='{.data.API_KEY}' | base64 -d)
curl -s http://127.0.0.1:18000/health                         # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18000/ready     # 200
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:18000/diagnose \
  -H 'Content-Type: application/json' -d '{"description":"x"}'            # 401
curl -s -X POST http://127.0.0.1:18000/diagnose -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"description":"Pedido criado no Salesforce nao aparece no SAP SD e nenhum erro foi exibido ao usuario","interface_type":"salesforce"}' \
  | python3 -m json.tool | grep -E 'llm_provider_used|agent_domain|matched_source'
kill %1
```

---

## 5. Como ler a evidência

Resultado de referência (2026-10-18):

| Verificação | Esperado |
|---|---|
| Rollout | 4 Deployments concluídos |
| Pod | `uid=1000(appuser)`, `/app/data` com dono `appuser` |
| Indexação | 15/15 documentos, 70 pontos em `sap_incident_docs` |
| `/health` · `/ready` | `{"status":"ok"}` · `200` |
| `/diagnose` sem chave | `401` |
| IDoc 51 | `200`, `llm_provider_used: rule_engine`, `matched_source: rule_engine:sap_idoc_status_51` |
| Salesforce sem erro visível | `200`, `llm_provider_used: ollama`, `agent_domain: saas`, `matched_source: salesforce_case_sap_sync_failure.md` |
| HPA | sobe para 2 réplicas sob carga e volta a medir após o restart |

Dois pontos de leitura:

- **Rule engine × LLM.** O primeiro diagnóstico é respondido sem LLM (DA-33).
  O segundo usa uma descrição que nenhuma regra casa — garantido por
  `tests/test_local_overlay.py` — para provar que o caminho do LLM local
  funciona dentro do cluster.
- **Confiança baixa não é falha da validação.** O diagnóstico pelo LLM veio com
  confiança 0,30: a validação prova que o pipeline roda no cluster, não mede a
  qualidade do diagnóstico (isso é papel de `scripts/eval_rag.py` e do
  promptfoo).

---

## 6. Problemas conhecidos

Todos encontrados na validação real de 2026-10-18 e já corrigidos.

| Sintoma | Causa | Correção |
|---|---|---|
| `'k3d' nao encontrado no PATH` | k3d não instalado | Seção 2 |
| Ingest no Pod: `PermissionError: '/app/data/.ingest_state_incidents.json'` | `/app/data` criado como root pelo `COPY` | Corrigido no `Dockerfile` (`install -d -o appuser`). Reconstrua a imagem (sem `--skip-build`) |
| O mesmo erro depois de corrigir o Dockerfile | Pod antigo seguia com a imagem anterior (tag fixa `:local`) | O script faz `rollout restart`; manualmente, passo 4.5 |
| Pod novo com `/ready` 503 e worker em `CrashLoopBackOff`, Pod antigo saudável | Secret regerado: Redis com a senha antiga, Pods novos com a nova | O script não regera mais. Para corrigir um cluster nesse estado: `kubectl -n integration-incident-copilot rollout restart deployment/redis` |
| Rollout parado em "old replicas are pending termination" | Pod novo não fica pronto | O script mostra eventos e logs dos Pods **não prontos**; o `/ready` informa qual serviço está degradado |
| `/ready` 503 com `ollama` degradado | Ollama ouvindo só em `127.0.0.1`, ou `hostAliases` ausente | Seção 2 e passo 4.5 |
| Evento `FailedComputeMetricsReplicas` logo após o restart | Pod ainda sem métricas | Transitório; o HPA volta a medir sozinho |

Para ver o corpo do `/ready` de dentro do Pod:

```bash
kubectl -n integration-incident-copilot exec deploy/integration-incident-copilot -- \
  python -c "import urllib.request as u;print(u.urlopen('http://127.0.0.1:8000/ready').read().decode())"
```

---

## 7. Limpeza

```bash
scripts/k3d-validate.sh --delete
docker image rm integration-incident-copilot:local   # opcional
```

Os logs em `reports/` ficam fora do versionamento.

---

## 8. Limites

- Não valida `APIRule`, Istio nem OIDC do BTP: isso só um cluster Kyma
  gerenciado prova.
- Qdrant e Redis sem persistência (`emptyDir` e `--save ""`): reiniciar o Pod
  apaga o índice — rode o passo 7 de novo.
- Sem Postgres: persistência de incidentes, metering e superfície admin ficam
  desligadas (`DATABASE_URL` vazio).

**Ver também:** [`deploy/local/README.md`](../deploy/local/README.md) ·
[`EVIDENCIA_K3D_2026-10-18.md`](EVIDENCIA_K3D_2026-10-18.md) ·
[`MANUAL.md`](MANUAL.md) §17 · [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) §3 e §12 ·
DA-24 em [`DECISOES_DE_ARQUITETURA.md`](DECISOES_DE_ARQUITETURA.md).
