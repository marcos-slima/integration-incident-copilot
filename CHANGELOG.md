# Changelog

Todas as mudanças relevantes do projeto são documentadas aqui.
Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/).

---

## [Unreleased]

Correções da auditoria técnica R3 (2026-10-20, `IIC-AUD-101`…`114`).

> **Rastreabilidade (IIC-AUD-103).** A mensagem do commit `400bce1` cita
> `IIC-AUD-051`, `053`, `058` e `082`, que não existem nos relatórios R1/R2
> (que vão até `044`). Os itens correspondem, pela descrição, a `030`/`051`
> (referências quebradas), `031` (`--reset`), `035`/`036` (DA-84/85) e `036`
> (`RollbackRequest`). A R3 numera a partir de `101` para não colidir.

### Corrigido
- **Build da imagem (IIC-AUD-101):** `Dockerfile` pedia `--extra reports`, removido do `pyproject.toml` no commit `400bce1`; `uv sync` falhava. Docstring de `scripts/generate_reports.py` atualizada.
- **Grafana (IIC-AUD-102):** `GF_SERVER_ROOT_URL` segue `GRAFANA_HOST_PORT` (default 3002); `scripts/start-docker.sh` usava 3001.
- **Compose:** serviço `postgres` passa a ler `POSTGRES_USER`/`POSTGRES_DB` (antes fixos em `iic`, divergindo do `DATABASE_URL` dos containers).
- **`Modelfile` versionado (IIC-AUD-103):** saiu do `.gitignore`; a correção do IIC-AUD-038 só existia na máquina local.
- **Âncoras (IIC-AUD-104):** índice de `docs/DECISOES_DE_ARQUITETURA.md`, links internos, `docs/TROUBLESHOOTING.md`, `docs/GOVERNANCA.md`, `docs/en/QUALITY_GATES.md`; seções não traduzidas do `docs/en/ARCHITECTURE_DECISIONS.md` apontam para o registro em português.
- **Índice de DAs (IIC-AUD-105):** números sem prosa declarados (pulos, notas informais, candidatas, DA-31 bloqueada).
- **DA-92 nos guias (IIC-AUD-106):** `README.md` e `GETTING_STARTED` (pt/en) sem `qwen3-coder-next` como recomendação.
- **Testes herméticos (IIC-AUD-107):** dois testes unitários baixavam `Qdrant/bm25` do Hugging Face.
- `scripts/check_models.py`: modelo duplicado (IIC-AUD-111).

### Alterado
- **Quality gate:** `docs_markup_integrity` valida âncoras (`#secao`, no arquivo e em outro `.md`); `das_index_current` exige que todo número de 1 até a maior DA esteja no índice ou declarado numa linha `| — |`.
- **Grafana somente leitura (IIC-AUD-110) — quebra de compatibilidade local:** o perfil `observability` exige `GRAFANA_DB_PASSWORD` e usa `iic_grafana_ro` por default (sem fallback para o dono do banco). Rode `alembic upgrade head` com `GRAFANA_DB_PASSWORD` no ambiente.
- **Versões fixadas (IIC-AUD-112):** base `python:3.12.15-slim` (= `.python-version`), `uv==0.12.17` no `Dockerfile` (= venv local), GitHub Actions por SHA.
- `.env.example`: portas do compose, `POSTGRES_USER/DB`, `FASTEMBED_BM25_PATH`, `SKILLS_DIR`; removido `MESSAGEPIT_SMS_FROM` sem consumidor (IIC-AUD-109).
- Cobertura (IIC-AUD-108): `tests/test_circuit_breaker_redis.py` (caminho Redis/Lua e degradação) e `tests/test_ingest_helpers.py`.

### Pendente
- **IIC-AUD-113:** re-baseline do promptfoo com o modelo de produção Kyma (`gpt-4o-mini`) — exige credenciais e rede.

---

## Correções da auditoria R4 (2026-10-20, `IIC-AUD-201`…`217`)

### Corrigido
- **IIC-AUD-201/303** Compose senhas: `90ddb67` havia trocado
  `${VAR:?REQUIRED}` por senhas padrão públicas. Agora as quatro variáveis
  (`POSTGRES_PASSWORD`, `GRAFANA_PASSWORD`, `GRAFANA_DB_PASSWORD`,
  `NEO4J_PASSWORD`) usam `${VAR:-REQUIRED_SET_IN_ENV}`: a interpolação sempre
  funciona, não há senha fraca e API/Postgres recebem o mesmo valor. O
  `.env.example` deixa as variáveis vazias/comentadas; `GETTING_STARTED`
  (pt/en) e os comentários do compose foram corrigidos.
- **IIC-AUD-202/309** Gate hermético: `_is_gitignored` aceita `root` e
  `_RUNTIME_ARTIFACTS` cobre os artefatos de runtime; testes em árvore sem
  `.git` adicionados.
- **IIC-AUD-203** `certifi` atualizado para `2026.10.19`; `pillow` elevado para
  `13.0.0` via `override-dependencies`. O `fastembed` só usa Pillow em modelos
  de imagem; o projeto usa `TextEmbedding`/`SparseTextEmbedding`, então o
  override não afeta o caminho de embeddings em uso.
- **IIC-AUD-205** DA-86 rebaixada para candidata; a prosa agora diz que
  endpoints e UI **não** foram implementados.
- **IIC-AUD-206** DA-80 rebaixada para candidata: o NER não está no caminho de
  dados e o Kyma usa `cloud_with_dlp` dependendo apenas do regex.
- **IIC-AUD-207** Nenhuma referência a docs privados em `app/`; `docs/MANUAL.md`
  e os docs EN foram alinhados; `docs/interno/` é tratado como artefato de
  runtime pelo gate, não como fonte versionada.
- **IIC-AUD-208** `IncidentRequest.tenant_id` agora chega a
  `build_incident_row` e é persistido em `incidents.tenant_id`.
- **IIC-AUD-209** Verificado via API: a conta e o repositório **não têm**
  GitHub Project v2 nem clássico; as duas issues abertas não têm
  `projectItems`. Não há board a atualizar.
- **IIC-AUD-210** Este CHANGELOG corrigido (as entradas anteriores declaravam
  como “corrigido” itens que ainda estavam parciais).
- **IIC-AUD-211** README atualizado para 1943 testes / 84 % / 10 276 linhas
  (1 688 descobertas).
- **IIC-AUD-212** `docs/MANUAL.md` Apêndice B: 8 símbolos com número de linha
  errado foram corrigidos (ex.: `_apply_confidence_guardrails` de 855 para 921,
  `verify_session_or_api_key` de 76 para 80). `docs/SECURITY.md` linha STRIDE
  de prompt descreve o digest do artefato (não rejeição em runtime) e §7.4
  mostra o comando/gate reais. `docs/TCO_...` voltou ao modelo canônico
  `qwen3.6:35b-a3b`.
- **IIC-AUD-213** Referências de `.env.example`, `ARQUETURA` e `worker.yaml`
  alinhadas.
- **IIC-AUD-214** Os seis docs de `docs/en/` sem cabeçalho receberam
  `source commit`.
- **IIC-AUD-215** Cobertura por pacote de risco no CI: `app/llm` 90 %,
  `app/agent` 90 % e `app/events` 85 % (medido em 2026-10-21), com piso de
  80 % por pacote — o piso global de 80 % sozinho escondia regressões.
- **IIC-AUD-216/313** `required-version = ">=0.12.17"` no `[tool.uv]`,
  `setup-uv` fixado em `0.12.17` nos workflows e docs atualizados.
- **IIC-AUD-204** Histórico purgado em 2026-10-21 com `git filter-repo` e
  force-push em todas as branches e tags. Verificação: **0 achados** de
  gitleaks no clone bare do remoto (462 commits). A árvore atual está limpa e
  o CI passou a varrer também o histórico completo (`fetch-depth: 0` +
  `gitleaks detect`). Os hashes de commit nos docs foram remapeados para o
  histórico novo.
- **IIC-AUD-217** Exceção **datada e com dono** para o modelo de produção Kyma:
  `deployed_model_exception` em `data/eval/promptfoo_baseline.json`
  (`gpt-4o-mini`, revisar até 2027-01-31). O gate continua WARN porque a
  qualidade **não foi medida** (faltam credenciais OpenAI/Azure); exceção
  expirada ou incompleta passa a FAIL, para a dívida não ser esquecida.

### Pendente / risco residual
- **IIC-AUD-204 (residual):** As refs ocultas `refs/pull/1/head`, `4/head` … `8/head`,
  de PRs já mergeados, ainda apontam para commits antigos com os valores
  removidos. O GitHub rejeita atualização (`deny updating a hidden ref`) e não
  há API para as apagar. Risco residual **aceito pelo mantenedor em
  2026-10-21**; a remoção definitiva depende de ticket no GitHub Support ou de
  recriar o repositório. A rotação das credenciais reais (`HomolAdmin-...`,
  chave Fernet) continua a cargo de quem tem acesso aos sistemas afetados.

---

## Correções da auditoria R5 (2026-10-21, `IIC-AUD-301`…`316`)

### Corrigido
- **IIC-AUD-301:** `retrieve_node` lia `connector_data` como `dict`; agora usa
  `getattr(..., "error_code", None)` e há teste com `ConnectorResult` real.
- **IIC-AUD-302:** normalização sem separadores (CamelCase/espaços/`_`),
  consultas com `initBindings` (sem aviso do rdflib), `next_steps` lidos de
  `skos:note "next_steps: ..."` e testes contra o TTL real.
- **IIC-AUD-304:** `ontology_debug` removido do grafo; §52 e as linhas de
  índice/registro da DA-61 declaram que só a Fase 1 está no caminho de dados e
  as Fases 2–6 são candidatas.
- **IIC-AUD-305:** `@observe_span("graph_enrich")` de volta em
  `graph_enrich_node`; `ontology_enrich_node` tem span próprio.
- **IIC-AUD-306:** `enrich_incident_with_ontology` e `infer_consistency`
  removidas.
- **IIC-AUD-307:** Piso `mcp>=1.9.0` corrigido para `mcp>=2.0,<3`.
- **IIC-AUD-308:** `ontology_ttl_path` resolvido a partir do pacote
  (`app/ontology/error_codes.ttl`), independente do cwd.
- **IIC-AUD-309:** Gate hermético (`_RUNTIME_ARTIFACTS` + `root`) com testes de
  árvore sem `.git`.
- **IIC-AUD-310:** Teste AMQP faz skip quando `SSL.present()` é `False`.
- **IIC-AUD-311:** `/llm` adicionado ao proxy do Vite.
- **IIC-AUD-312:** `langchain-openai>=1.6.0` alinhado no extra; ruff hook
  atualizado para `v0.16.10`.
- **IIC-AUD-313:** `required-version`, pin do `setup-uv` e docs do uv alinhados.
- **IIC-AUD-314:** Worker Kyma usa `RQ_REDIS_URL` via Secret em vez de `--url`
  na linha de comando; liveness probe adicionado.
- **IIC-AUD-315:** Os `nosec` de `salesforce_connector.py`, `i18n/__init__.py`
  e `llm/origins.py` **foram mantidos**: ao remove-los, `bandit -ll` volta a
  reportar B608/B704/B104 como Medium (não são obsoletos; cada um tem
  justificativa na linha). O job de segurança continua verde.
- **IIC-AUD-316:** `mypy` adotado de forma incremental. `[tool.mypy]` cobre 9
  módulos de contrato/configuração/qualidade (`app/config.py`,
  `app/connectors/base.py`, `app/contracts/observe.py`, `app/contracts/odata.py`,
  `app/evaluation/coverage.py`, `app/evaluation/gates.py`,
  `app/ontology/enrichment.py`, `app/ontology/feedback.py`,
  `app/services/incident_recorder.py`), sem `ignore_errors`; 28 erros
  corrigidos e passo `mypy` no CI. O `app/` legado continua fora do escopo até
  os stubs de Starlette/LangGraph/rdflib serem resolvidos.

### Pendente
- Nenhum item aberto da R5.

---

## Correções da auditoria R6 (2026-10-21, `IIC-AUD-401`…`419`)

Relatório: `AUDITORIA_R6_IIC_2026-10-21.md` (Project, pasta `claude/`). Três
correções anteriores estavam incompletas e foram refeitas: **313** (pin do
Python), **201/303** (sentinela de senha) e **205** (prosa da DA-86).

### Corrigido
- **IIC-AUD-401** `.python-version` passa a fixar só a série `3.12`. Nenhuma
  versão do uv até a 0.12.17 oferece CPython 3.12.15 para download (a lista
  termina em 3.12.14), então `uv python install` falhava no CI e `uv sync`
  falhava em máquina sem 3.12.15 de sistema. O patch exato continua na imagem
  (`python:3.12.15-slim`). Comentários do `pyproject.toml` e do `tests.yml`
  corrigidos.
- **IIC-AUD-402** `deploy/kyma/apirule.yaml` migrado para
  `gateway.kyma-project.io/v2` (`hosts` com short host name, `noAuth: true`).
  O v1beta1 foi removido do módulo API Gateway. Manifest validado contra o
  schema do CRD publicado em `kyma-project/api-gateway`, não contra um cluster.
  Overlay `deploy/local`, README do Kyma, `DEPLOYMENT.md`, `ARCHITECTURE.md`
  (pt/en) e a prosa da DA-24 atualizados; teste novo em
  `tests/test_kyma_manifests.py`.
- **IIC-AUD-403** O sentinela `REQUIRED_SET_IN_ENV` deixou de ser uma senha
  utilizável: Postgres, Grafana e Neo4j têm entrypoint que **recusa subir** com
  ele (verificado com as imagens reais), e a API recusa `DATABASE_URL` ou
  `NEO4J_PASSWORD` que o contenham
  (`app/main.py::_ensure_no_compose_placeholder_secrets`).
- **IIC-AUD-404** Docs que descreviam `${VAR:?}` e o erro `required variable
  ... is missing a value` (que não existe mais) reescritos: `DEPLOY.md` (pt/en),
  `GETTING_STARTED.md` (pt/en), `MANUAL.md`, `README.md`, `README.pt-BR.md`,
  `CLAUDE.md` e o comentário do worker no compose.
- **IIC-AUD-405 — quebra de compatibilidade local:** no modo container a
  persistência é **opt-in**. `DATABASE_URL` da API, do worker e do reporter vem
  só de `CONTAINER_DATABASE_URL`; vazio = sem banco. Antes, o default apontava
  sempre para o serviço `postgres`, que só existe no perfil `observability`: sem
  ele, cada `/diagnose` tentava gravar num host inexistente e o boot exigia
  `LLM_CREDENTIALS_MASTER_KEY`. **Ação:** quem usa o perfil `observability`
  com a API em container precisa definir no `.env`
  `CONTAINER_DATABASE_URL=postgresql+asyncpg://iic:<senha>@postgres:5432/iic`
  (`scripts/start-docker.sh` já deriva o valor do `DATABASE_URL` nativo).
  O job `compose-smoke` passou a subir com `.env` vazio, provando que a API
  sobe sem master key e sem banco.
- **IIC-AUD-406** DA-86: a prosa (DECISOES §77, índice, `CLAUDE.md`, registro
  em inglês) agora descreve os 4 endpoints de registro de versões que existem
  em `/admin/api/knowledge/...`, e diz que eles **não alteram o índice RAG**.
  A DA continua candidata.
- **IIC-AUD-407** DA-70: nomes das métricas Prometheus alinhados com
  `app/metrics.py` (`iic_*`; `iic_diagnosis_latency_seconds` em vez de
  `diagnosis_duration_seconds`, que não existia). Registrado que os dashboards
  fornecidos são SQL e que `/metrics` exige um Prometheus externo; KPI do
  `ROADMAP.md` reescrito como PromQL.
- **IIC-AUD-408** Testes que nunca rodavam no CI passam a rodar:
  `test_checkpointing_postgres.py` e `test_bloco3_observabilidade.py` no job
  `migrations_and_dashboards` (24 testes verdes contra Postgres real nesta
  rodada), `test_admin_ui_browser.py` no novo job `browser` (Playwright via
  `uv run --with`, 6 testes verdes) e `test_security.py` com master key gerada
  por teste. A duplicata de cifra em `test_security_audit.py` foi removida.
- **IIC-AUD-409** Testes herméticos: `test_contracts_observe.py` não roda mais
  o pipeline real ao emitir drift (a emissão é registrada), e
  `test_ingest.py` não abre conexão com o Qdrant de `:6333`. A suíte termina
  sem aviso de conexão do `qdrant_client`.
- **IIC-AUD-410** `REQUIRE_AUTH=true` passa a exigir também `ADMIN_API_KEY`;
  `secret.example.yaml` ganhou `ADMIN_API_KEY` e `SESSION_SECRET`, e
  `scripts/k3d-validate.sh` cria a chave admin.
- **IIC-AUD-411** Worker do compose recebe a URL do Redis por `RQ_REDIS_URL`,
  não mais por `--url` na linha de comando.
- **IIC-AUD-412** Healthcheck do Postgres usa `POSTGRES_USER`/`POSTGRES_DB`.
- **IIC-AUD-413** `USER_GUIDE.md` (pt/en) e o docstring do ingest registram que
  `--reset` não remove chunks de arquivos apagados/encolhidos (use
  `--reset-collection`); `scripts/start-docker.sh --reset-rag` passou a usar
  `--reset-collection`, como a mensagem já prometia. O `INGEST_REFERENCE.md` do
  Project foi reescrito contra o `--help` atual.
- **IIC-AUD-414** Scripts de tradução avulsos (4 `.py` com caminho absoluto da
  máquina local e 2 `.txt`) movidos para `docs/interno/` (ignorado); os três
  scripts manuais restantes estão listados em `CLAUDE.md`.
- **IIC-AUD-415** `DEPLOYMENT.md` declarado fonte de verdade de implantação;
  `DEPLOY.md` (pt/en) escopado como detalhe e troubleshooting do Compose.
- **IIC-AUD-416** `EMBEDDING_MODEL` removido do ConfigMap Kyma (ignorado com
  `EMBEDDING_BACKEND=fastembed`).
- **IIC-AUD-418** Números de testes e cobertura atualizados no `README.md` e no
  `MANUAL.md` (1957 passed, 17 skipped; 83 %).
- **IIC-AUD-419** (achado durante a validação da R6) Com o embedder (Ollama)
  fora do ar, `/diagnose` respondia 500 mesmo nos casos que o rule engine
  resolve. A DA-65 passou a cobrir o embedder: falha de **transporte**
  (`ConnectionError`, `httpx.TransportError`) degrada para "sem contexto";
  erro de aplicação continua propagando
  (`app/rag/retriever.py::_embed_query_or_none`). Verificado com a imagem real
  no compose sem Ollama: diagnóstico `rule_engine:sap_idoc_status_51`, sem 500.

### Sem mudança de código
- **IIC-AUD-417** (Info) `mypy app` completo tem 114 erros em 34 arquivos;
  segue a adoção incremental do `[tool.mypy]`.

### Pendente
- **IIC-AUD-402:** validar o APIRule v2 num cluster Kyma
  (`kubectl apply --dry-run=server -k deploy/kyma`) quando o trial liberar.
- Cabeçalhos `source commit` dos docs de `docs/en/` alterados nesta rodada
  apontam para o commit anterior até o próximo commit.


## [1.4.0] — 2026-10-20

> **Numeração.** A tag `v1.3.0` já existe no remoto apontando para um commit
> experimental (`8aeb64b`, linha v1.3) que não é ancestral do `master`. Para
> não reescrever uma tag publicada, esta release usa o próximo minor
> disponível, **1.4.0**.

Lote entregue entre 19 e 20/10, mais a auditoria técnica de 2026-10-20
(25 achados, `IIC-AUD-001`…`025`). O status abaixo é o real — o que está
entregue, o que é schema-only e o que voltou para candidata.

| DA | Status | Evidência / observação |
|---|---|---|
| DA-75 | Entregue | `interrupt()` nativo do LangGraph + `Command(resume=...)`; `/incidents/{id}/review` usa checkpoint |
| DA-76 | Entregue | `ai_warning` em `DiagnosisResponse`, catálogo pt-BR/en e relatório |
| DA-79 | Candidata | Schema (`tenant_id`) e filtros de repositório existem; isolamento não é fail-closed em todos os caminhos |
| DA-82 | Schema-only | Tabelas `snapshot_table`/`rollback_log`; endpoint removido até existir mecanismo real |
| DA-83 | Entregue | `explanation` em `DiagnosisResponse` e `_build_xai_explanation` |
| DA-84 | Candidata | Tabela, ORM e repositório; endpoints de versão/rollback ausentes |
| DA-85 | Candidata | Tabela, ORM e repositório; endpoints e `_run_ab_test()` ausentes |
| DA-86 | Entregue | Endpoints de versões/histórico/rollback; UI de histórico ainda pendente |
| DA-87 | Entregue | `/admin/metrics/domains` com janela `days`; rota duplicada `/admin/dashboard/domains` removida |
| DA-88 | Entregue | `app/scripts/load_test.py`, config e `tests/test_load_testing.py` (3 testes) |
| DA-89 | Entregue | Export PDF/XLSX de compliance |
| DA-90 | Entregue | Documentação GRC e testes de conformidade |
| DA-91 | Entregue | Testes RBAC com status exatos; rotas DA-87/89 exercitadas |
| DA-92 | Registrada | Modelo canônico `qwen3.6:35b-a3b` desde 2026-10-13; re-baseline do promptfoo pendente (DA-51) |

### Correções da auditoria de 2026-10-20

- **001/015** HITL nativo: escalonamento antes do LLM, `hitl_request` no topo do
  estado, retomada por `Command(resume=...)`, sem conexão Postgres própria.
- **002/005/021** Admin/RBAC: `/admin/deploy/rollback` removida; testes exigem
  `401`/`503` exatos; `/admin/dashboard/domains` removida e
  `/admin/metrics/domains` aceita `days`.
- **003/004/016** Livro-razão: DA-79/84/85 rebaixadas para candidatas e o
  processo de entrega em lote registrado como dívida (histórico preservado).
- **006/028** ORM × migrations: `Snapshot`/`RollbackLog`, índices e colunas de
  gateway alinhados; `alembic check` verde nas migrations 022–028.
- **007/013/017** CI: `nosec B704` justificado, `conftest` isola Redis/Neo4j,
  job instala `--extra prometheus`.
- **008** API: `ai_warning` e `explanation` chegam a `/diagnose`.
- **DA-74** Prompt `en` medido com promptfoo em 2026-10-20 (`qwen3.6:35b-a3b`,
  17 casos (12 via LLM + 5 via rule engine), 0 falhas); digest em
  `locales.en`. Gate passa a 21 PASS / 2 WARN.
- **CI** Compose smoke recebe as senhas de interpolação e um `.env` com chave
  efêmera; healthcheck do Qdrant usa `bash` (a imagem não tem `python3`);
  espera do Neo4j sem `IndentationError`; `aiohttp` 3.14.3 → 3.14.5 (CVEs).
- **CI (promptfoo)** `llm_eval` deixa de rodar no agendado — runner hospedado
  sem Ollama/Qdrant — e passa a `workflow_dispatch` com input `runner`;
  `actions/setup-node` e `astral-sh/setup-uv`; sem infraestrutura o job falha
  com mensagem explícita, em vez de verde falso.
- **009/010/011/012/014/018/019/022/023** Documentação e higiene: tabela de DAs
  sem duplicatas, seções únicas, `--reset-collection` na troca de embedding,
  Grafana em 3002, Python fixado, `kyma-config` fora do índice, 14 configs de
  debug e WARNs conhecidos documentados.
- **020/024/025** Sem ação de código: bundle movido para fora do repositório;
  falha AMQP é do ambiente; nenhum segredo na árvore.

### Versionamento

A versão canônica desta release é **1.4.0** em `pyproject.toml`, `app/main.py`
e `app/a2a/agent_card.py`. As entradas `2.0.0`/`2.0.1` abaixo são históricas de
uma numeração experimental anterior e não devem ser usadas para release; o
próximo lote, quando houver, será **1.5.0** (minor). A tag `v1.3.0` também é
histórica: aponta para a linha experimental antiga e não descreve este código.

---

## [Não lançado] — validação 2026-10-06/07 (branch `fix/validacao-2026-10-06`)

Correções da auditoria de 2026-10-05, em blocos, cada um verificado em clone
limpo. O detalhe item a item está nos relatórios de cada bloco e nas
mensagens de commit.

### Documentação (Bloco 5)
- **Removidos** `docs/UC_01..09`, os dois documentos de auditoria e o
  `TESTING_E2E_USER_FLOW`: descreviam código inexistente e marcavam como
  corrigidos achados abertos.
- **Novo** `docs/CASOS_DE_USO.md`: nove cenários, funcional e técnico, com
  números medidos por `tests/test_casos_de_uso.py`.
- **Reescritos** `docs/CONNECTORS.md` (variáveis reais de cada conector) e
  `docs/TROUBLESHOOTING.md` (readiness em `/ready`, não `/health`).
- **Novo gate** `docs_env_vars`; `docs_code_references` passa a conferir
  intervalos `arquivo.py:10-20`.
- `REFERENCE_LIBRARY_FALLBACK_ENABLED` (M-24), `false` no deploy Kyma.

### Correções pós-DA-66/67/69/70 e posicionamento (2026-10-17/18, branch `docs/en-translation`)
Contagens de testes conferidas com `pytest --collect-only` em 2026-10-18.

- **DA-67** Orçamento por diagnóstico (`app/llm/budget.py`): reserva sobre o
  custo estimado, débito do custo real, `BudgetExhaustedError` →
  `FailureClass.BUDGET_EXHAUSTED`; `DIAGNOSIS_BUDGET_USD` e
  `LLM_BUDGET_RESERVE_RATIO`. Tokenizer com fallback `len//4` para qualquer
  falha de carga, sem retentativa. Testes: `test_budget_ledger.py` (15),
  `test_tokenizer_offline.py` (8).
- **DA-69** Checkpoint sem PII em claro (`app/agent/checkpointing.py`, redação
  no `put`/`put_writes`, inclusive valores inline no jsonb); `PostgresSaver`
  montado corretamente (`from_conn_string` era context manager; URL SQLAlchemy
  normalizada); `thread_id` passado por nome. Testes: `test_checkpointing.py`
  (15), `test_checkpointing_postgres.py` (1, marker `postgres_smoke`, validado
  contra Postgres real).
- **DA-66** `test_llm_routing.py` (18).
- **DA-71** Resultado da busca web sanitizado e delimitado antes de voltar ao
  LLM (`format_untrusted_web_hits`). Testes: `test_web_search_injection.py` (16).
- **DA-29** `reranker_invariant` por significância (McNemar pareado), não por
  vencedor pontual; benchmark refeito com n=90; mmarco mantido.
- **Avaliação** Dataset RAG de 28 para 110 casos (90 no escopo, 20 fora):
  Hit@1 95,6% (86/90), Hit@3 97,8%, MRR 0,967, 19/20 fora de escopo rejeitados.
- **DA-24** Bundle validado em Kubernetes real (k3d): overlay `deploy/local/`,
  `scripts/k3d-validate.sh`, `test_local_overlay.py` (10). Bugs corrigidos:
  `/app/data` sem escrita para `appuser` (`Dockerfile`), Secret regerado a cada
  execução, Pod não recriado com tag fixa. Evidência:
  `docs/EVIDENCIA_K3D_2026-10-18.md`; tutorial:
  `docs/TUTORIAL_K3D_KUBERNETES_LOCAL.md`.
- **Dependências** `tiktoken`, `langgraph-checkpoint-postgres`,
  `psycopg[binary]` declaradas; migrations 017/018 na convenção numérica.
- **UI** a camada de apresentação passa a mostrar o que o backend já devolvia.
  React: sinal de escalonamento (DA-44), evidências com `trust_level` e aviso
  para `simulated`/`web_untrusted` (DA-25/DA-71), proveniência (DA-53),
  soberania via `GET /llm/policy` (DA-43/DA-66) e mensagens por `code`, com
  `error_id`. Admin UI: proveniência, sinal e custo no detalhe do incidente;
  coluna "Sinal" na lista; evidência renderizada pelos campos reais (antes
  caía em JSON cru).
- **API** `BudgetExhaustedError` e `PolicyViolationError` deixam de virar 500
  "erro interno": 503 com `code` (`budget_exhausted`, `policy_violation`) e
  `error_id`; o motivo completo, que cita origens internas, fica só no log.
  Todos os erros de `/diagnose` ganham `code`. Testes:
  `test_gateway_refusal_http.py` (4).
- **DA-74** Conteúdo do diagnóstico no idioma pedido (`locale` em
  `/diagnose`, A2A, MCP e Event Mesh; a UI envia o idioma da tela): rule
  engine, avisos de confiança e relatório em pt-BR ou en; prompt `en` com
  digest próprio (pt-BR e `en` medidos; `locales.en` gravado em 2026-10-20);
  e-mail/SMS de ativação no idioma de quem cadastrou; motivos de
  soberania, de roteamento e notas dos conectores com código estável,
  traduzidos pelas UIs. Corrige: regras do TTL sem `probableRootCause`
  devolviam "Erro conhecido detectado via taxonomia SKOS." no lugar da causa
  curada de `rules.py`, e passos com vírgula eram quebrados em pedaços.
  Migration 021 (`incidents.locale`, `web_users.locale`); gate
  `content_i18n`. Testes: `test_i18n_content.py` (34).
- **DA-73** UIs no idioma do navegador: UI web (`react-i18next`) e Admin UI
  (Jinja + JS, catálogo embutido sem quebrar a CSP) em pt-BR e en, com seletor
  de idioma compartilhado (cookie `iic_locale`), datas/números/USD por `Intl`,
  erros com `code` estável traduzidos pela UI, pseudo-locale `?lng=xx` e gate
  `ui_i18n`. Corrige o `<html lang="en">` da UI web com conteúdo em português.
  Testes: `test_i18n.py` (23).
- **DA-72** Roteamento por registro de modelos: no modo gerenciado cada modelo
  ativo é candidato do Policy Engine, com parâmetros por modelo (transporte,
  janela de contexto, capacidades que só restringem a origem, domínios, nível,
  prioridade, limite mensal bloqueante), score custo × latência
  (`LLM_ROUTING_COST_WEIGHT`/`LLM_ROUTING_LATENCY_WEIGHT`), circuito por origem,
  failover pela lista e latência gravada por chamada. A decisão passa a respeitar
  os overrides da Admin UI (antes a lista de candidatos vinha sempre do `.env`).
  `/admin/models`: edição dos parâmetros, orçamento e pesos como overrides, e
  simulador (`GET /admin/api/routing/preview`). Provenance: `llm_model` registra o
  modelo que respondeu. Migration 020. Invariante 9 emendada. Testes:
  `test_registry_routing.py` (23), `test_registry_routing_repository.py` (5).
- **DA-67 (correção)** o roteamento buscava o preço registrado pelo rótulo do
  provider (`"openai"`) e nunca o encontrava: a estimativa caía na tabela
  interna (~33× o preço de `gpt-4o-mini`) e podia barrar o candidato por
  orçamento. Agora busca pelo modelo efetivo; no modo gerenciado, pelo default
  da origem. Testes: `test_llm_routing.py::TestCostEstimation` (+4).
- **Migration 019** `incidents.should_escalate`, `escalation_reason`,
  `cost_usd` e `llm_calls` (custo debitado do orçamento do diagnóstico).
  Testes: `test_incident_escalation_cost.py` (6).
- **Validação** suíte unitária na máquina de desenvolvimento: 1608 passando,
  cobertura 81,13% (antes das entregas de DA-71, avaliação e k3d); quality
  gate sem falha.

> As entradas abaixo são históricas: descrevem o projeto na data delas
> (ex.: a busca web hoje é configurada em `web_search_sources`, DA-57, e não
> tem fallback genérico).

---

## [1.2.0] — 2026-09-15

### Adicionado

**Web search direcionada por interface_type (v1.2)**
- Busca web agora usa fontes específicas por protocolo:
  - `rfc` → help.sap.com/docs/SAP_NETWEAVER + community.sap.com + github.com/SAP/PyRFC
  - `odata` → help.sap.com + community.sap.com/technology-blogs-by-sap
  - `cap` → cap.cloud.sap + github.com/SAP/cloud-cap-samples + community.sap.com
  - `servicenow` → developer.servicenow.com + community.sap.com
  - `salesforce` → developer.salesforce.com + community.sap.com
  - `ariba` → help.sap.com/docs/ARIBA + community.sap.com
  - `apim` → help.sap.com/docs/SAP_API_MANAGEMENT + community.sap.com
  - fallback genérico para casos sem interface_type
- Threshold configurável via `.env`: `WEB_SEARCH_THRESHOLD=0.6` (default)
- Web search desligável via `WEB_SEARCH_ENABLED=false`

---

## [1.1.0] — 2026-09-15

### Adicionado

**Web search fallback (v1.1)**
- Busca web via DuckDuckGo (`ddgs`) ativa automaticamente quando o melhor
  resultado RAG tem score < 0.6 — RAG local continua sendo o caminho primário
- Busca direcionada para SAP Community, GitHub SAP e SAP Help Portal
  (`site:community.sap.com OR site:github.com/SAP OR site:help.sap.com`)
- Retorna título, URL e resumo dos 5 melhores resultados, incorporados
  ao contexto do prompt como fonte secundária
- Privacidade: usa apenas a descrição textual do incidente, nunca dados
  do conector (que podem conter informações sensíveis do cliente)
- Novo node `web_search` no grafo LangGraph, entre `retrieve` e `diagnose`
- Compatível com GraphRAG opt-in — entra no pipeline antes do `graph_enrich`

---

## [2.0.1] — 2026-09-15

### Corrigido (double check P0/P1/P2)

- **P1 — Prompt injection** — sanitização em todos os campos não confiáveis
  (`description`, `logs`, `payload`, chunks do RAG) via `sanitize_untrusted_input()`
  em `app/agent/nodes.py`; neutraliza padrões conhecidos antes de entrar no prompt LLM
- **P2 — Imagens Docker fixadas** — `ollama/ollama:0.34.1`, `neo4j:5.26.0-community`
  (qdrant:v1.19.0 já estava fixado); reprodutibilidade garantida em todos os serviços
- **P2 — Ollama local** — atualizado para v0.34.1 (alinhado com docker-compose.yml)

---

## [2.0.0] — 2026-09-15

## [1.0.0] — 2026-09-15

Primeira versão estável. Pipeline completo de diagnóstico de incidentes de integração SAP e multi-vendor, com interface web, 8 conectores (4 validados contra sistema real), camada A2A, GraphRAG opt-in e documentação de produto completa.

### Adicionado

**Pipeline de diagnóstico (agente)**
- Orquestração via LangGraph (connector → retrieve → diagnose → report)
- GraphRAG opt-in via Neo4j (graph_enrich / graph_write, desligado por default)
- Guardrails determinísticos: teto de confiança 0.4 para fallback de conector, 0.3 para ausência de contexto RAG
- Validação estruturada de saída do LLM via with_structured_output(DiagnosisModel) com fallback tolerante
- Seed fixo (seed=42) para reproducibilidade com temperature=0

**Interface web**
- Frontend React servido pelo FastAPI em / (static/index.html)
- Formulário completo: descrição, sistema de origem, identificador, logs, payload (campos avançados)
- Resultado: causa raiz, badge de confiança colorido, próximos passos numerados, relatório Markdown expansível
- Histórico de diagnósticos da sessão e status da stack
- Funciona também como arquivo standalone no browser

**API e protocolos**
- REST: POST /diagnose, GET /health (FastAPI + Pydantic)
- A2A (Agent2Agent): Agent Card, JSON-RPC 2.0 (message/send, tasks/get) — protocolo aberto Linux Foundation
- Documentação interativa Swagger em /docs

**Conectores (8)**
- ODataConnector — OAuth2 Client Credentials + OData v2/v4
- RFCConnector — pyrfc 3.3.1 + SDK 7.50 PL19 · validado: ABAP Cloud Trial A4H rel 754
- ServiceNowConnector — Table API REST · validado: PDI real
- SalesforceConnector — OAuth2 + REST · validado: Developer Edition
- WorkdayConnector — OAuth2 + REST (sandbox gratuito indisponível)
- AribaConnector — OAuth2 + REST (sandbox incompatível)
- CAPConnector — OData v4 + XSUAA · validado: BTP Trial (HANA Cloud)
- APIManagementConnector — schema especulativo, não validado

**RAG e base de conhecimento**
- Busca híbrida dense + sparse BM25, fusão RRF via Qdrant
- Base de conhecimento local em data/sample_docs/ (10 documentos)
- Indexador incremental com suporte a .md e .pdf

**LLM Gateway plugável**
- qwen3-coder-next:latest como modelo de produção (MoE 80B/3B ativo, 262K ctx)
- Suporte a Ollama (default), OpenAI, Azure OpenAI e OpenRouter

**Qualidade e CI**
- 68 testes (54 unitários + 14 de integração)
- GitHub Actions: lint + testes a cada push/PR
- pre-commit: ruff, gitleaks, check-yaml, check-added-large-files
- Duas comparações formais de modelo via promptfoo (pipeline real)

**Documentação**
- docs/GETTING_STARTED.md — do zero ao primeiro diagnóstico em 10 minutos
- docs/USER_GUIDE.md — guia de produto com orientações de prompt, casos típicos, glossário SAP
- docs/ARCHITECTURE.md — detalhamento técnico por camada
- docs/PROCESSO_DESENVOLVIMENTO.md — 12 fases de desenvolvimento
- docs/TUTORIAL_ARQUITETURA_DEBUG.md — roteiro de debug no VS Code
- Diagramas Mermaid em todos os documentos principais

### Fora do escopo desta versão (roadmap)
- Validação real de Workday e Ariba
- APIManagementConnector com schema real
- Multi-tenancy CAP (MTX + Service Manager)
- Integração inbound A2A com Joule (aguardando GA Q4/2026)
- Autenticação JWT/OAuth2 na API
- Persistência de histórico entre sessões

---

O desenvolvimento foi feito em 12 fases documentadas em
docs/PROCESSO_DESENVOLVIMENTO.md.

---

*Integration Incident Copilot · github.com/marcos-slima/integration-incident-copilot*
