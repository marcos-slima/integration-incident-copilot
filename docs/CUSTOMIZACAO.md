# Customização — adaptar o Integration Incident Copilot ao seu ambiente

Guia para quem vai implantar o produto num cliente e precisa ajustá-lo:
LLM, soberania de dados, orçamento, base de conhecimento, regras, roteamento,
prompt, conectores, busca web e limiares. Cada seção diz **onde** mudar,
**como** conferir e **o que a mudança quebra** se feita sem cuidado.

Regra geral: tudo o que é configuração vai no `.env` (modo nativo) ou no
`ConfigMap`/`Secret` (Kubernetes). A configuração efetiva, com segredos
mascarados, sai de:

```bash
uv run python -m app.config
```

Depois de qualquer mudança de código ou de dado versionado:

```bash
uv run pytest tests/ -m "not integration" -q
PATH="$PWD/.venv/bin:$PATH" uv run python scripts/quality_gate.py
```

| Quero mudar… | Seção | Tipo de mudança |
|---|---|---|
| Provedor e modelo de LLM | [1](#1-provedor-e-modelo-de-llm) | Configuração |
| Para onde dado confidencial pode ir | [2](#2-soberania-de-dados) | Configuração |
| Teto de custo | [3](#3-orçamento-e-limites-de-tempo) | Configuração |
| Documentos que o diagnóstico consulta | [4](#4-base-de-conhecimento) | Dado + reindexação |
| Erros conhecidos respondidos sem LLM | [5](#5-regras-do-rule-engine) | Código ou TTL |
| Qual especialista atende cada sistema | [6](#6-roteamento-skills-declarativas) | YAML |
| O texto do prompt | [7](#7-prompt-de-diagnóstico) | Código + medição |
| Um sistema novo (conector) | [8](#8-conectores) | Código |
| Busca web | [9](#9-busca-web) | Configuração + admin |
| Catálogo de sistemas e usuários | [10](#10-administração) | Admin |
| Quando o diagnóstico é escalado | [11](#11-limiares-de-confiança-e-escalonamento) | Código |
| Opcionais (GraphRAG, checkpoint, filas) | [12](#12-recursos-opcionais) | Configuração |

---

## 1. Provedor e modelo de LLM

**Onde:** `.env` / `ConfigMap`.

| Variável | Valores | Observação |
|---|---|---|
| `LLM_PROVIDER` | `ollama` · `openai` · `azure_openai` | Primário |
| `LLM_FALLBACK_PROVIDER` | idem, ou vazio | Usado só em falha de transporte (DA-20/68) |
| `LLM_MODEL` | nome do modelo | Default `qwen3.6:35b-a3b` |
| `OLLAMA_HOST` | URL | Ollama local ou remoto |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | chave / URL | `OPENAI_BASE_URL` vazio = API oficial; preenchido = qualquer endpoint compatível (vLLM, Groq, LiteLLM, gateway corporativo) |
| `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_DEPLOYMENT` | | Azure OpenAI |

`openai` é só um rótulo: a política olha a **origem real** (a URL). Um
`OPENAI_BASE_URL=http://localhost:8000` é tratado como local (DA-45).

**Registro gerenciado (opcional, DA-46/47/72).** Com `LLM_REGISTRY_DB=true` e
`DATABASE_URL`, modelos e credenciais vêm da tela `/admin/models` (credenciais
cifradas com `LLM_CREDENTIALS_MASTER_KEY`), e não do `.env`. Cada modelo ativo
é um candidato: o agente escolhe entre todos pelas regras de soberania,
capacidade, janela de contexto, domínio, nível, limite mensal, circuito e
orçamento, e ordena os elegíveis por custo × latência (pesos
`LLM_ROUTING_COST_WEIGHT`/`LLM_ROUTING_LATENCY_WEIGHT`), prioridade e padrão da
origem. Se o primeiro falhar por transporte, tenta o próximo. Registro vazio ou
ilegível é erro (fail-closed), nunca volta ao `.env` em silêncio. Antes de
ativar, use **Simular roteamento** na tela para ver a decisão.

**Conferir:** `GET /llm/policy` mostra, para cada provedor, a origem resolvida e
se ela pode receber dado `public` e `confidential`.

**Cuidado:**
- Trocar de modelo invalida a comparação de qualidade medida: o gate
  `deployed_model_evaluated` avisa quando o modelo implantado não tem baseline.
  Meça com o promptfoo (seção 7) antes de comparar resultados.
- Modelos diferentes aceitam parâmetros diferentes (seed, `response_format`,
  tools); a tabela fica em `app/llm/capabilities.py`, por origem. No registro,
  marque "não suporta" no modelo que tiver menos que a origem (a origem é o
  teto; o modelo só restringe).

---

## 2. Soberania de dados

| Variável | Default | Efeito |
|---|---|---|
| `DATA_SOVEREIGNTY_MODE` | `strict` | Nada classificado como confidencial sai da máquina |
| `CONFIDENTIAL_ALLOWED_ORIGINS` | vazio | Origens (URLs) que podem receber dado confidencial; só vale com `cloud_with_dlp` |
| `SENSITIVITY_DEFAULT` | `confidential` | Classificação de texto livre sem sinal mais forte |

Para liberar um provedor de nuvem para dado confidencial:

```bash
DATA_SOVEREIGNTY_MODE=cloud_with_dlp
CONFIDENTIAL_ALLOWED_ORIGINS=https://<recurso>.openai.azure.com
```

Dado de conector real sempre é confidencial; o cliente da API pode **elevar** a
classificação (`sensitivity_level`), nunca rebaixar (DA-43). Entradas
inválidas na allowlist nunca ampliam a permissão.

**Cuidado:** `SENSITIVITY_DEFAULT=public` faz texto livre ir para a nuvem. A
redação de PII (`app/redaction.py`) cobre e-mail, CPF, CNPJ, número de IDoc,
tokens e senhas — não nomes próprios nem endereços.

---

## 3. Orçamento e limites de tempo

| Variável | Default | Efeito |
|---|---|---|
| `LLM_GATEWAY_MAX_COST_USD` | 0,50 | Teto por chamada |
| `DIAGNOSIS_BUDGET_USD` | 1,00 | Teto por diagnóstico (soma das chamadas) |
| `LLM_BUDGET_RESERVE_RATIO` | 0,25 | Reserva sobre o custo estimado antes de aceitar um candidato |
| `LLM_REQUEST_TIMEOUT_SECONDS` | 90 | Por chamada ao LLM |
| `DIAGNOSIS_TIMEOUT_SECONDS` | 180 | Pipeline inteiro |
| `LLM_ROUTING_COST_WEIGHT` / `LLM_ROUTING_LATENCY_WEIGHT` | 0,7 / 0,3 | Pesos do score no registro gerenciado (DA-72) |

Com banco configurado, orçamento, teto por chamada e pesos também são editáveis
em `/admin/models` (overrides de runtime; o valor do banco vence o `.env`).

Quando o orçamento do diagnóstico não cobre nenhum candidato, a chamada para
com `BudgetExhaustedError` (sem retry nem failover, DA-67/68). Se houver um
candidato mais barato (ex.: Ollama local, custo 0), o roteamento o escolhe.

**Preços:** com o registro gerenciado, os preços por milhão de tokens vêm de
`llm_models` (`price_in_per_1m`/`price_out_per_1m`); sem ele, de uma tabela
interna aproximada. A estimativa de tokens usa `cl100k_base` — aproximação para
modelos que não são da OpenAI.

---

## 4. Base de conhecimento

**Onde:** documentos `.md`, `.pdf` ou `.epub` em `data/knowledge_base/`. Vazia,
o ingest usa `data/sample_docs/` (15 documentos de exemplo).

```bash
uv run python -m app.rag.ingest --target incidents             # incremental
uv run python -m app.rag.ingest --target incidents --reset-collection
uv run python -m app.rag.ingest --help
```

O formato recomendado (Sintoma / Causas comuns / Diagnóstico / Resolução
típica) está em [`GETTING_STARTED.md`](GETTING_STARTED.md) §6.

**Cuidado:**
- O embedding usado na ingestão tem de ser o mesmo da consulta
  (`EMBEDDING_BACKEND`, `EMBEDDING_MODEL`). Trocar exige reindexar com
  `--reset-collection`; o `embedding_guard` rejeita a consulta se divergir.
- O estado da ingestão fica em disco (`data/.ingest_state_*.json`): depois de
  apagar o Qdrant, reindexe com `--reset-state`.
- Os números de avaliação (`scripts/eval_rag.py`) valem para `data/sample_docs`.
  Com documentos próprios, crie casos em `data/eval/rag_eval_dataset.json` e
  meça de novo; o gate `corpus_coverage` exige que as `expected_keywords`
  existam no documento esperado.

---

## 5. Regras do rule engine

Erros conhecidos são respondidos **sem LLM**, antes de qualquer chamada
(DA-33). Duas fontes, carregadas juntas:

| Fonte | Arquivo | Quando usar |
|---|---|---|
| Regras em código | `app/agent/rules.py::KNOWN_ERROR_RULES` | Padrão regex + causa + próximos passos |
| Taxonomia SKOS | `app/ontology/error_codes.ttl` (`ONTOLOGY_TTL_PATH`) | Conceitos com `skos:altLabel`, `ex:confidence` e notas de padrões e passos |

Uma regra em código:

```python
ErrorRule(
    patterns=[r"BAPI.*authority.*check", r"no authorization for"],
    category="sap_bapi_authorization",
    probable_root_cause="Usuário técnico sem autorização para a BAPI chamada.",
    next_steps=["Conferir o objeto de autorização na SU53 do usuário técnico."],
    confidence=0.85,
)
```

**Conferir:**

```bash
uv run python -c "from app.agent.rules import match_known_error; print(match_known_error('no authorization for BAPI_PO_CREATE1', has_connector_data=False))"
```

**Cuidado:**
- Padrões amplos demais roubam casos que deveriam ir ao LLM. Teste com
  descrições que **não** devem casar.
- `RULE_ENGINE_ENABLED=false` desliga a camada inteira (invariante 2: o default é ligado).
- Os casos do promptfoo declaram se esperam `rule_engine` ou `llm`
  (`metadata.path`); um teste confere isso, e uma regra nova pode exigir
  ajustar esses casos.

---

## 6. Roteamento (skills declarativas)

**Onde:** `data/skills/diagnose_incident.yaml` (DA-63). O bloco `domains` decide
qual especialista atende cada `interface_type`:

```yaml
domains:
  sap: [odata, rfc, cap, po]
  saas: [servicenow, salesforce, workday, ariba, successfactors]
```

Fora das duas listas, o domínio sai da descrição (especialista genérico). O
mesmo arquivo declara risco, escopos e timeout da tool no Capability Registry
(DA-27), que é fail-closed: tool sem entrada é negada.

**Cuidado:** o `Dockerfile` copia `data/skills/`; sem ele o supervisor não
sobe. O gate `connector_reachable` confere que todo conector tem domínio.

---

## 7. Prompt de diagnóstico

**Onde:** `app/agent/prompts.py` (personas, template e instrução de saída, com
versão e digest — DA-53). Inclui os `Field(description=)` do `DiagnosisModel`,
que o LangChain injeta no schema.

Mudar o texto **reprova** o gate `prompt_digest_measured` até você medir e
regravar o baseline:

```bash
npx promptfoo eval -c promptfooconfig.yaml -o resultados.json
PATH="$PWD/.venv/bin:$PATH" uv run python scripts/quality_gate.py --compare-promptfoo resultados.json
PATH="$PWD/.venv/bin:$PATH" uv run python scripts/quality_gate.py --write-prompt-baseline
```

**Cuidado:** regravar o baseline sem rodar o promptfoo apaga a garantia de que
o prompt em produção foi medido. O arquivo `data/eval/prompt_baseline.json` é
essa declaração.

---

## 8. Conectores

Dez conectores registrados (SAP OData, RFC, CAP, PO/PI, API Management,
ServiceNow, Salesforce, Workday, Ariba, SuccessFactors). Para cada um, as
variáveis de modo real e o status de validação estão em
[`CONNECTORS.md`](CONNECTORS.md) §2 e na matriz de
[`ARCHITECTURE.md`](ARCHITECTURE.md). Sem as variáveis, o conector roda em
modo de demonstração e o diagnóstico marca a evidência como `simulated`.

Adicionar um sistema novo exige 9 superfícies — passo a passo em
[`CONNECTORS.md`](CONNECTORS.md) §4; os gates `connector_reachable` e
`connector_coverage` reprovam se faltar alguma.

**Cuidado:** "tem conector" não é "foi validado". Só RFC, ServiceNow, Salesforce
e CAP foram testados contra instância real.

---

## 9. Busca web

| Variável | Default | Efeito |
|---|---|---|
| `WEB_SEARCH_ENABLED` | desligada | Interruptor principal |
| `WEB_SEARCH_POLICY` | `approved` | `disabled` · `approved` · `public_only` |
| `WEB_SEARCH_THRESHOLD` | 0,6 | Só busca quando o RAG local fica abaixo |

As fontes permitidas por `interface_type` (filtro de site e termo técnico)
ficam na tabela `web_search_sources`, editável em `/admin/web-search` (DA-57).
Sem fonte aprovada, não há busca (fail-closed).

A consulta que sai é sanitizada (sem a descrição original, sem PII, IDs ou
tokens) e o resultado que volta é sanitizado e delimitado como dado não
confiável antes de chegar ao LLM (DA-71). A neutralização de injection é por
padrões: reduz o risco, não o elimina.

---

## 10. Administração

Com `DATABASE_URL` e `ADMIN_API_KEY`, a UI `/admin` oferece: modelos e
credenciais, uso real de tokens, catálogo de sistemas integrados (correlação de
incidentes, DA-49/50), histórico de incidentes, usuários da UI e fontes de busca
web. Detalhes em [`USER_GUIDE.md`](USER_GUIDE.md) ("Administração").

Em produção, fixe `API_KEY`, `A2A_API_KEY`, `EVENT_MESH_API_KEY` e
`ADMIN_API_KEY` e use `REQUIRE_AUTH=true`; chaves vazias são geradas no boot e
mudam a cada reinício (cenário D de [`DEPLOYMENT.md`](DEPLOYMENT.md)).

### Idioma das interfaces (DA-73)

A UI web e a Admin UI seguem o idioma do navegador (`pt-BR` ou `en`; outro
idioma cai em `en`). O seletor de idioma grava o cookie `iic_locale`, que vale
para as duas. O conteúdo do diagnóstico (rule engine, avisos, relatório, texto
do LLM) segue o `locale` do pedido, que a UI preenche com o idioma da tela
(DA-74); sem `locale`, `pt-BR`.

Para **adicionar um idioma** (ex.: `es`):

1. Copie `frontend/src/i18n/locales/en.json` para `es.json` e
   `app/i18n/locales/admin.en.json` para `admin.es.json`, e traduza os valores
   (as chaves e as variáveis `{{x}}` ficam iguais).
2. Inclua `es` em `SUPPORTED_LOCALES` e `LOCALE_LABELS` de
   `frontend/src/i18n/index.ts` e de `app/i18n/__init__.py`, e em
   `normalizeLocale`/`normalize_locale` (idioma base `es` → `es`).
3. Inclua `es` em `LOCALES` de `app/i18n/checks.py`, para o gate `ui_i18n`
   conferir a paridade do catálogo novo.
4. Conteúdo do diagnóstico: copie `app/i18n/locales/content.en.json` para
   `content.es.json` e traduza — inclusive `rules.<categoria>`, uma entrada por
   regra do rule engine —, inclua `es` em `CONTENT_LOCALES`
   (`app/i18n/content.py`) e escreva a instrução de idioma em
   `OUTPUT_LANGUAGE_INSTRUCTIONS` (`app/agent/prompts.py`). O gate
   `content_i18n` aponta o que faltar.
5. Meça o prompt novo antes de citar qualidade: use o idioma como 4ª parte
   do prompt (`descrição|||interface|||identificador|||es`). Para `en`, o
   arquivo `prompts/case.en.txt` já faz isso:

   ```bash
   npx promptfoo eval -c promptfooconfig.yaml -p prompts/case.en.txt -o resultados-en.json
   PATH="$PWD/.venv/bin:$PATH" uv run python scripts/quality_gate.py --write-prompt-baseline --prompt-locale en
   ```

   Para outro idioma, crie um `prompts/case.<idioma>.txt` equivalente e troque
   `--prompt-locale`. Até medir, o gate `prompt_digest_measured` avisa que o
   idioma não foi medido.
6. `cd frontend && npm run build`, e confira com `?lng=es`.

Uma regra nova do rule engine (seção 5) precisa da causa e dos passos em cada
idioma não-default (`rules.<categoria>` nos catálogos de conteúdo); o gate
`content_i18n` reprova sem eles.

Para revisar texto que tenha escapado do catálogo, abra qualquer tela com
`?lng=xx`: todo texto do catálogo aparece entre `⟦ ⟧`; o que ficar sem a marca
é texto fixo.

---

## 11. Limiares de confiança e escalonamento

O sinal de escalonamento (DA-44) é determinístico, em `app/agent/escalation.py`:
`FLOOR_TIER_MIN_EVIDENCE` (0,62) e `CURATED_TIER_MIN_EVIDENCE` (0,45) decidem
quando a evidência é fraca demais para confiar no diagnóstico.

**Cuidado:** esses valores foram calibrados contra o reranker em produção e o
corpus de exemplo. Com outra base ou outro reranker, recalibre medindo — não
ajuste para "o teste passar". O reranker é invariante do projeto (DA-29); o
gate `reranker_invariant` só aceita outro se ele for medido em
`data/eval/reranker_benchmark_results.json`.

---

## 12. Recursos opcionais

| Recurso | Variável | Exige |
|---|---|---|
| GraphRAG (histórico de incidentes) | `GRAPH_RAG_ENABLED=true` | Neo4j |
| Checkpoint e retomada | `CHECKPOINTING_ENABLED=true` | `DATABASE_URL` (o checkpoint grava estado redigido) |
| Fila assíncrona e eventos | `REDIS_URL`, worker | Redis |
| Event Mesh / AMQP | `AMQP_ENABLED=true` | Broker AMQP 1.0 |
| Tracing | `LANGFUSE_*` | Langfuse |
| Métricas Prometheus | `PROMETHEUS_ENABLED=true` | — |

---

## O que não é customizável sem decisão de arquitetura

Os invariantes de `AGENTS.md` §4 só mudam com uma DA nova (processo em
`CLAUDE.md`, "Como registrar uma nova DA"). Os mais prováveis de aparecer numa
customização: o reranker em produção, o rule engine ligado por default, toda
chamada de LLM pelo `app/llm/gateway.py` e a política fail-closed do Capability
Registry.
