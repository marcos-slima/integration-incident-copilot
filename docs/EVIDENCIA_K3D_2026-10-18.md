# Validação em Kubernetes real — k3d (2026-10-18)

Cenário G (Kubernetes genérico, ver `deploy/local/README.md`): o bundle `deploy/kyma/` aplicado sem cópia
num Kubernetes real, via o overlay `deploy/local/` e `scripts/k3d-validate.sh`.
Logs completos gerados localmente em `reports/k3d-validation-20261018-114432.log` e
`reports/k3d-validation-20261018-115014.log` (diretório fora do versionamento).

## Ambiente

| Item | Valor |
|---|---|
| Cluster | k3d v5.9.0, k3s v1.35.5+k3s1, 1 nó, containerd 2.2.3 |
| Cliente | kubectl v1.35.9, Kustomize v5.7.1 |
| Host | GMKtec EVO-X2, Ubuntu (kernel 7.0.0-38) |
| LLM | Ollama no host via `host.docker.internal` (`hostAliases` → 172.18.0.1), soberania `strict` |

## Resultado

| Verificação | Resultado |
|---|---|
| Rollout | `qdrant`, `redis`, `integration-incident-copilot`, `integration-incident-copilot-worker`: concluído |
| Pod da API | `uid=1000(appuser)`; `/app/data` com dono `appuser` |
| Indexação dentro do Pod | 15/15 documentos de `data/sample_docs` |
| `GET /health` | `{"status":"ok"}` |
| `GET /ready` | `200` |
| `POST /diagnose` sem `X-API-Key` | `401` |
| `POST /diagnose` IDoc 51 | `200`, `rule_engine:sap_idoc_status_51` |
| `POST /diagnose` timeout OData via CPI (1ª execução) | `200`, `rule_engine:http_timeout` |
| `POST /diagnose` Salesforce sem erro visível (2ª execução) | `200`, **`llm_provider_used: ollama`**, `agent_domain: saas`, `matched_source: salesforce_case_sap_sync_failure.md`, confiança 0,30 |
| Índice | 70 pontos em `sap_incident_docs` |
| HPA | escalou de 1 para 2 réplicas sob carga (`cpu: 398%/70%`) |

## Bugs encontrados e corrigidos

1. **`/app/data` sem escrita para o `appuser`** — o `COPY` do Dockerfile
   criava o diretório como root, e `python -m app.rag.ingest` falhava no Pod
   com `PermissionError` em `.ingest_state_incidents.json`. Afetava também o
   Kyma. Corrigido no `Dockerfile` (`install -d -o appuser`), com teste.
2. **Secret regerado a cada execução do script** — a `REDIS_PASSWORD` mudava
   só para os Pods novos; o Redis em execução seguia com a antiga, o `/ready`
   dava `503` e o worker entrava em `CrashLoopBackOff`. O script passou a criar
   o Secret uma vez por cluster.
3. **Pod não recriado após importar a imagem** — com a tag fixa `:local`, o
   `apply` sem mudança de spec mantinha o Pod antigo. O script faz
   `rollout restart`.

## Limites desta validação

- O caminho do LLM local foi exercitado só na 2ª execução (a 1ª teve os dois casos
  resolvidos pelo rule engine); o caso usado é garantido fora do rule engine por
  `tests/test_local_overlay.py`.
- A confiança do diagnóstico pelo LLM ficou em 0,30: o pipeline funciona no cluster,
  mas a qualidade do diagnóstico não é o que esta validação mede.
- Não cobre o que é específico do Kyma: `APIRule`, Istio, OIDC do BTP.
- Qdrant e Redis sem persistência; sem Postgres (persistência e admin
  desligados).
- Um evento `FailedComputeMetricsReplicas` aparece logo após o restart
  (métricas de Pod ainda não pronto); o HPA voltou a medir em seguida.
