# Roteiro de avaliação — o produto em ~30 minutos

Para quem quer decidir se o Integration Incident Copilot serve ao seu ambiente:
o que rodar, o que observar e como interpretar. Nenhuma credencial de sistema
real é necessária — os conectores rodam em modo de demonstração e a resposta
marca isso.

| Etapa | Tempo | O que você verifica |
|---|---|---|
| 0. Subir | 10–15 min (o download do modelo pode levar mais) | A stack responde |
| 1. Erro conhecido | 2 min | Diagnóstico sem LLM, com evidência |
| 2. Erro sem regra | 3 min | Diagnóstico pelo LLM, com fonte citada |
| 3. Fora de escopo | 2 min | O produto admite quando não sabe |
| 4. Soberania | 2 min | Para onde o dado pode ir |
| 5. Números de qualidade | 5 min | De onde vêm os indicadores |
| 6. (Opcional) Kubernetes | 15 min | O mesmo bundle num cluster real |

---

## 0. Subir

Siga [`GETTING_STARTED.md`](GETTING_STARTED.md) até a API responder. Caminho mais
curto, tudo em container (cenário C de [`DEPLOYMENT.md`](DEPLOYMENT.md)):

```bash
./scripts/start-docker.sh
curl -s http://localhost:8000/health      # {"status":"ok"}
curl -s http://localhost:8000/ready       # "ok" ou o serviço degradado
```

Fixe uma chave para os testes (no `.env`: `API_KEY=...`) e exporte-a:

```bash
export API_KEY=<a mesma do .env>
```

A UI web fica em `http://localhost:8000`.

---

## 1. Erro conhecido — resposta sem LLM

```bash
curl -s -X POST http://localhost:8000/diagnose -H "X-API-Key: $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"description":"IDoc travado com status 51","interface_type":"rfc","identifier":"RFC-IDOC-51-DEMO"}' \
  | python3 -m json.tool
```

**Observe:**

| Campo | Esperado | Significado |
|---|---|---|
| `llm_provider_used` | `rule_engine` | Resolvido por regra determinística, custo zero |
| `matched_source` | `rule_engine:sap_idoc_status_51` | Qual regra respondeu |
| `prompt_version` | `null` | Nenhum prompt produziu esta resposta |
| `evidence[].trust_level` | inclui `simulated` | O dado do conector é de demonstração — e a resposta diz isso |

Detalhe do caso: [`CASOS_DE_USO.md`](CASOS_DE_USO.md), UC-01.

---

## 2. Erro sem regra — diagnóstico pelo LLM

```bash
curl -s -X POST http://localhost:8000/diagnose -H "X-API-Key: $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"description":"Pedido criado no Salesforce nao aparece no SAP SD e nenhum erro foi exibido ao usuario","interface_type":"salesforce"}' \
  | python3 -m json.tool
```

**Observe:** `llm_provider_used` com o provedor configurado (ex.: `ollama`),
`agent_domain: saas` e `matched_source: salesforce_case_sap_sync_failure.md` —
o documento da base que fundamentou a resposta. `prompt_version` e
`prompt_digest` identificam o prompt medido.

**Interprete:** `diagnosis_confidence` é limitada pela evidência, não pela
autoavaliação do modelo. Confiança baixa com fonte certa significa "a direção
está certa, confirme no sistema".

---

## 3. Fora de escopo — o produto admite quando não sabe

```bash
curl -s -X POST http://localhost:8000/diagnose -H "X-API-Key: $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"description":"impressora do SAP GUI nao imprime relatorio"}' \
  | python3 -m json.tool
```

**Observe** (resultado documentado em [`CASOS_DE_USO.md`](CASOS_DE_USO.md), UC-03):

| Campo | Esperado | Significado |
|---|---|---|
| `agent_domain` | `generic` | Nenhum especialista se aplica |
| `matched_source` | `null` | Nenhum documento foi admitido como fonte |
| `diagnosis_confidence` | `0.0` | O guardrail não deixa o LLM afirmar sem evidência |
| `escalation.escalation` | `no_context` | Sinal determinístico: revisão humana |
| `evidence` | só a descrição (`user_reported`) | Não há fato de sistema nem documento |

É o comportamento esperado: a base de exemplo trata de integração, não de
impressão. O LLM é chamado, mas a resposta sai marcada como sem fundamento.

---

## 4. Soberania — para onde o dado pode ir

```bash
curl -s http://localhost:8000/llm/policy -H "X-API-Key: $API_KEY" | python3 -m json.tool
```

**Observe:** para cada provedor, a origem real (URL) e se ela pode receber dado
`public` e `confidential`. No default (`strict`), nada confidencial sai da
máquina. Como mudar: [`CUSTOMIZACAO.md`](CUSTOMIZACAO.md) §2.

---

## 5. Números de qualidade — de onde vêm

| Indicador | Valor (2026-10-18) | Como reconferir |
|---|---|---|
| Recuperação (110 casos: 90 no escopo, 20 fora) | Hit@1 95,6% (86/90), Hit@3 97,8%, MRR 0,967; 19/20 fora de escopo rejeitados | `EMBEDDING_BACKEND=fastembed uv run python scripts/eval_rag.py` com o Qdrant indexado |
| Reranker | Nenhum candidato significativamente melhor que o de produção (n=90) | [`RERANKER_BENCHMARK.md`](RERANKER_BENCHMARK.md) |
| Invariantes do projeto | Quality gate sem falha | `PATH="$PWD/.venv/bin:$PATH" uv run python scripts/quality_gate.py` |
| Testes | suíte unitária | `uv run pytest tests/ -m "not integration" -q` |

**Interprete com cuidado:** os números valem para os 15 documentos de exemplo em
`data/sample_docs`. Com a sua base, meça de novo (ver
[`CUSTOMIZACAO.md`](CUSTOMIZACAO.md) §4).

---

## 6. (Opcional) Kubernetes

O mesmo bundle do SAP BTP Kyma num cluster k3s local, com API, worker, Qdrant,
Redis e o LLM local: [`TUTORIAL_K3D_KUBERNETES_LOCAL.md`](TUTORIAL_K3D_KUBERNETES_LOCAL.md).

```bash
scripts/k3d-validate.sh
```

---

## O que este roteiro não mostra

- **Integração real:** só 4 dos 10 conectores (RFC, ServiceNow, Salesforce, CAP)
  foram validados contra instância real; a matriz está em
  [`ARCHITECTURE.md`](ARCHITECTURE.md).
- **Kyma gerenciado:** `APIRule`, Istio e OIDC do BTP não foram validados.
- **Qualidade com a sua base:** os indicadores acima são do corpus de exemplo.

Limitações completas: [`USER_GUIDE.md`](USER_GUIDE.md) ("Limitações conhecidas").
