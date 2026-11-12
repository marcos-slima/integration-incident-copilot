# Rastreabilidade do Integration Incident Copilot

Inspeção em 05/11/2026 (São Paulo). Checkout local `master`, SHA
`16728f9f90fd65a49004e31e07bd5b3bb1d1247b`, versão `1.4.0` em
`pyproject.toml`. O recorte usa arquivos rastreados dessa revisão. O
`git status --short` identificou material local não rastreado no dossiê,
no tutorial Python/TypeScript e em Joule Studio, além de um diretório
excluído da inspeção por conter material sensível. A referência é local.

A tabela vincula os principais caminhos inspecionados ao ponto de
implementação. “Inspecionado” indica leitura estática. Evidência de
execução aparece separadamente. Consulte a [arquitetura](ARCHITECTURE.md),
o [manual](MANUAL.md) e as [decisões](DECISOES_DE_ARQUITETURA.md).

| Capacidade | Entrada | Código | Configuração e persistência | Teste identificado | Alcance |
|---|---|---|---|---|---|
| REST e UI | `POST /diagnose` com `IncidentRequest` | `app/main.py::diagnose` → `app/agent/graph.py::run_diagnosis` | Sessão ou chave da API, 10/min por IP. Resultado `DiagnosisResponse`. Gravação de incidente depende de banco. | `tests/test_graph_e2e.py` e `tests/test_gateway_refusal_http.py` | Implementado |
| Fila | `POST /diagnose/async` e `GET /diagnose/async/{job_id}` | `app/queue.py::enqueue_diagnosis`, `run_diagnosis_job`, `get_job_status` | `REDIS_URL` e worker RQ. 202 com job, 503 se fila indisponível, 404 se job ausente ou expirado. | `tests/test_queue.py` | Implementado, dependência opcional |
| MCP | Streamable HTTP em `/mcp` | `app/mcp/server.py::diagnose_incident` e `list_connectors`, autorização em `app/mcp/policy.py` | Middleware com a mesma chave REST e política fail-closed. A ferramenta adapta argumentos para o diagnóstico. | `tests/test_mcp.py`, `tests/test_mcp_policy.py` | Implementado para o catálogo registrado |
| A2A | Agent Card e `POST /a2a` | `app/a2a/server.py`, `task_manager.py`, `task_store.py` | Chave A2A, JSON-RPC `message/send` e `tasks/get`, tarefas em Redis ou memória | `tests/test_a2a.py`, `tests/test_a2a_task_store.py` | Subconjunto implementado |
| Eventos | `POST /events/incident` ou consumidor AMQP | `app/events/consumer.py`, `amqp_consumer.py`, `idempotency.py`, `app/queue.py::run_event_job` | CloudEvents 1.0, tipo fixo, deduplicação por origem e ID. Com Redis, webhook aguarda enfileiramento e AMQP aceita após enqueue. Sem Redis, webhook usa BackgroundTasks. AMQP rejeita payload inválido ou excedente de reentregas e usa MODIFIED em falha recuperável. | `tests/test_events.py` | Transportes implementados. Validação em broker requer evidência por cenário |
| Orquestração | `run_diagnosis` | `app/agent/graph.py::build_graph`, `app/agent/nodes.py`, `rules.py`, `supervisor.py` | Supervisor → conector → recuperação → especialista → relatório. 22 regras no fallback Python e 28 na carga combinada observada. O nó opcional de enriquecimento parte de `report`, que também tem aresta para END. | `tests/test_graph_e2e.py`, `tests/test_graph_timeout.py` | Implementado. Watchdog limita espera. O slot é liberado quando a execução termina |
| Revisão humana | `POST /incidents/{incident_id}/review` | `app/main.py`, `app/agent/graph.py`, `app/agent/checkpointing.py` | `CHECKPOINTING_ENABLED` desabilitado por padrão. Interrupção retomável depende de estado e thread do grafo. PostgresSaver com redação. | Inspeção da ligação do checkpointer | Implementado como opção |
| Verificação posterior | `POST /incidents/{incident_id}/verify` | `app/main.py::verify_incident_endpoint` | Efeitos independentes sobre Neo4j, incidente relacional e feedback Langfuse, segundo precondições de cada destino. | `tests/test_graph_store.py` cobre o armazenamento de grafo | Implementado. Data de verificação e veredito de acerto têm significados distintos |
| Gateway | Invocação do especialista e `GET /llm/policy` | `app/llm/gateway.py::invoke_via_gateway`, `routing.py::evaluate_routing`, `registry_routing.py` | Elegibilidade antes da pontuação, origem efetiva, capacidades, sensibilidade, orçamento e circuito. Registry pode alterar destino efetivo. Ollama, OpenAI e Azure OpenAI. | `tests/test_llm_gateway.py` | Implementado. DA-92 tem baseline histórica do Qwen e revalidação atual pendente |
| Recuperação | Nó `retrieve` | `app/rag/retriever.py`, ingestão em `scripts/ingest.py` | Qdrant, coleções de incidentes e referência. Embedding compatível com o índice. RRF e reranker DA-29. Fallback de referência habilitado por padrão. | `tests/test_da65_rag_degradation.py`, `tests/test_rag_quality.py` | Implementado. Ranking e sigmoid são escores de ordenação, sem calibração de diagnóstico |
| GraphRAG | `graph_enrich`, `graph_write` e verificação | `app/agent/graph.py`, `app/rag/graph_store.py` | `GRAPH_RAG_ENABLED` e Neo4j. Histórico distinto da taxonomia RDF | `tests/test_graph_store.py`, `tests/test_graph_store_neo4j_smoke.py` | Opt-in |
| Conectores | Par `interface_type` e `identifier` | `app/connectors/__init__.py::_REGISTRY`, `base.py` | Os dez caminhos e as variáveis estão em [Conectores](CONNECTORS.md) | Contratos simulados no guia de conectores | RFC tem evidência histórica de logon. ServiceNow, Salesforce e CAP têm relatos de ambiente real no repositório. A cobertura datada de cada cenário exige revisão dos artefatos originais |
| Versionamento RAG | Rotas admin de versões, histórico e rollback | `app/admin/routes.py::rollback_knowledge_version`, `app/admin/models.py` | PostgreSQL. O rollback muda `is_current` nas linhas de versão | `tests/test_rag_versioning.py` | API parcial. O handler inspecionado não restaura vetores no Qdrant |
| Prompts e A/B | Repositórios de serviço | `app/services/prompt_version_repository.py`, `app/services/ab_test_repository.py`, `app/agent/prompts.py` | ORM e repositórios. Versão/digest do prompt do diagnóstico têm trilha própria | Testes de repositório citados no histórico DA-84/85 | Fluxo completo de seleção e medição de variantes não confirmado |
| Rollback de deployment | Schema | `app/admin/models.py`, migrations da DA-82 | `snapshot_table` e `rollback_log` | Inspeção estática | Schema apenas. Revisão de imagem, downgrade Alembic e restauração de dados são operações distintas |

## Divergências e limites

| Registro | Resolução |
|---|---|
| 500 com Qdrant desligado em 07/10/2026 | Preservado como achado histórico. `_retrieve_hybrid` e `_retrieve_dense_only` tratam as exceções de consulta cobertas pela DA-65. O ensaio ASGI posterior confirmou degradação para a regra de IDoc com conexão recusada e embeddings sintéticos. Continua pendente o teste com o backend real de embeddings. |
| RFC apenas mock | Registro atual inclui `RFCConnector` com dependência pyrfc e modo real. O relato de `RFC_SYSTEM_INFO` em ABAP Trial cobre logon, com leitura real de status de IDoc pendente. |
| Modelo canônico aprovado | `app/config.py` declara `qwen3.6:35b-a3b`. A baseline histórica de 13/10/2026 registra o Qwen. A revisão atual e o modelo declarado no Kyma exigem evidência própria. |
| Rollback único | DA-82 descreve schema de deployment. DA-86 expõe alteração de versão relacional via API admin. A confirmação do conteúdo servido exige verificar a coleção RAG. |
| Kyma em operação | Manifests e evidência estática precisam ser lidos separadamente da [validação local k3d](EVIDENCIA_K3D_2026-10-18.md). A validação gerenciada permanece pendente. |

## Registro da inspeção

Contagens estáticas deste recorte: 22 declarações no fallback Python, 29 arquivos
Python em `alembic/versions` e 72 linhas de índice no formato
`| DA-N |` em `CLAUDE.md`. O maior ID observado é DA-92. As linhas
do índice incluem registros históricos e candidatos.

A inspeção usou `git status --short`, `git branch --show-current`,
`git rev-parse HEAD` e `git log --oneline -20`. Histórico do checkout:

```text
16728f9 fix(gates): pular data/.ingest_state_*.json em check_docs_code_references
f6d3730 gitignore: pular docs/glossario-iic.pdf no build Docker
753b38f docs: PDF glossário IIC formatado corretamente
52579c5 docs: glossário IIC com siglas e PDF de consulta rápida
b26cef7 fix(docs): remover referência a app/validation/pii.py não existente
400bce1 fix(DA-18/26/28/29/33/34/37/38/39/40/41/51/53/58/82): audit findings round-2 (IIC-AUD-026..044) - IIC-AUD-028: REQUIRE_AUTH=true no Kyma ConfigMap e secret - IIC-AUD-026: Redis --requirepass quoting fix (list form) - IIC-AUD-029: Isolar feature flags em tests/conftest.py - IIC-AUD-033/37: INFRA_PROBE_TIMEOUT e 8 settings faltantes no .env.example - IIC-AUD-038/39: Modelfile qwen3.6:35b-a3b, promptfoo@0.124.1 - IIC-AUD-040: Remover reports extra redundante - IIC-AUD-041: scripts/ingest.py --source falha ao divergir - IIC-AUD-030/34: docs_code_references estende a docs/*.md + gate duplicate DA - IIC-AUD-051: docs_code_references fixa 22 refs quebradas - IIC-AUD-053: --reset semantics docs/MANUAL.md - IIC-AUD-058: DA-84/85 testes de repositório (0% → 100% cobertura) - IIC-AUD-082: CLAUDE.md Remove RollbackRequest chore: atualizar CLAUDE.md com RollbackRequest removido
ba63b0f docs: registra validacao estatica do Kyma e tags imutaveis (#8)
f45ada7 chore(release): 1.4.0 (#7)
436926c chore(release): 1.3.0 (#6)
e0640be fix(ci): torna llm_eval manual e corrige actions do promptfoo (#5)
72556e5 Merge pull request #4 from marcos-slima/docs/en-translation
7019e4e docs(DA-74): mede o prompt en e zera o WARN do quality gate
bf26891 fix(ci): destrava compose, Qdrant, Neo4j e pip-audit
5abb269 docs(IIC-AUD-014/016): changelog, divida de processo e sync EN
d67fea8 chore(IIC-AUD-011/012/018/019): alinha reset, Grafana, Python e config Kyma
e140507 test(DA-88): cobre request, timeout e percentis do load test
1ca4892 fix(IIC-AUD-009/010/092): deduplica livro-razao, secoes unicas e gates
ccf0cb3 docs: rebaixa DA-79/84/85 para candidatas e alinha indice (achados 003/004/016)
d86df48 fix(DA-82/84/85/86): ORM x migrations e cadeia 022-028
55944cf fix(DA-75/76/83): HITL nativo, /review por Command(resume) e campos de resposta

```

### Verificações executadas

- A inspeção e os testes usaram uma cópia rastreada feita com
  `git archive HEAD` em `/tmp/iic-audit` e o Python 3.12 do ambiente
  virtual do projeto.
- `python -m pytest --noconftest tests/test_da65_rag_degradation.py -p no:cacheprovider -q`
  teve 1 teste aprovado e 1 falha. O caso híbrido tentou baixar o
  artefato BM25 via fastembed antes de chegar à consulta mockada. O
  sandbox bloqueou a resolução DNS. Com um plugin temporário de teste
  substituindo apenas `_sparse_query_vector` por um vetor sintético,
  o mesmo arquivo passou com 2 testes. Isso exercita o tratamento da
  consulta, com embeddings simulados.
- A suíte `python -m pytest tests -m 'not integration' -p no:cacheprovider -q`
  foi iniciada, sem resultado conclusivo nesta rodada.
- O Mermaid CLI renderizou os 28 blocos de `docs/ARCHITECTURE.md`
  para SVG em diretório temporário após executar fora do sandbox e
  corrigir um identificador reservado. Isso valida sintaxe e geração,
  com revisão visual detalhada ainda pendente.
- Permanecem pendentes o diagnóstico HTTP com backend real de embeddings,
  a validação dos provedores e brokers e a reexecução das evidências
  históricas. A matriz inclui os caminhos de administração, segurança e operação
  inspecionados abaixo. Validações ambientais permanecem abertas.

## Complemento de administração, segurança e operação

| Capacidade | Entrada e código | Configuração e persistência | Teste ou evidência | Status |
|---|---|---|---|---|
| SPA e login | `frontend/src/App.tsx`, `app/auth.py` | Cookie de sessão assinado, cadastro relacional ou usuários estáticos conforme configuração | `tests/test_auth.py` | Implementado |
| Administração | `app/admin/ui.py`, `app/admin/routes.py` sob `/admin` | Shell Jinja, API com chave administrativa, PostgreSQL nas operações persistidas | `tests/test_admin_routes.py`, `tests/test_admin_ui_csp.py` | Implementado por operação |
| Modelos e uso | `/admin/api/models`, `/admin/api/credentials`, `/admin/api/runtime-config`, `/admin/api/usage` | Registry gerenciado, Fernet e metering conforme configuração | `tests/test_admin_runtime.py`, `tests/test_admin_crypto.py` | Implementado, condicionado à configuração |
| Usuários e ativação | `/admin/api/users`, `/auth/verify/email`, `/auth/verify/phone` | PostgreSQL, Mailpit ou Resend para e-mail, etapa de telefone fora da aplicação | `tests/test_auth.py`, `app/notifications/providers.py` | Fluxo implementado com entrega SMS externa ao IIC |
| Catálogo de sistemas | `/admin/api/systems`, `/admin/api/incidents` | `integration_systems` e correlação por `system_key`; ambiguidade exige identificação explícita | `tests/test_admin_systems.py` | Implementado |
| Observação de contrato | `scripts/check_contract_drift.py` → `app/contracts/observe.py::observe` | Parser de metadata, fingerprint canônico, baseline append-only em `system_contracts` | `tests/test_contracts_observe.py`, `tests/test_contracts_e2e.py` | CLI implementada. A emissão chama o consumidor em processo. Operação agendada e publicação externa não confirmadas |
| Isolamento por locatário | `IncidentRequest.tenant_id`, filtros em repositórios | Campos e índices em incidentes e credenciais. Escopo ausente em consulta pode ser global | Inspeção de `app/admin/repository.py` e `app/services/incident_repository.py` | Parcial |
| Localização | `frontend/src/i18n/index.ts`, `app/i18n/__init__.py`, `app/i18n/content.py` | Interface usa query, cookie e idioma do navegador, com fallback em inglês. Conteúdo de diagnóstico usa `locale`, com fallback pt-BR. A SPA deriva o campo de sua seleção | Catálogos e resolução inspecionados | Implementado |
| Métricas e relatórios | `app/metrics.py`, `/admin/metrics/domains`, `/admin/reports/compliance/export` | Prometheus depende de flag e token, Grafana consulta PostgreSQL e Langfuse é opcional | `.github/workflows/quality.yml` valida queries e migrations | Superfícies implementadas. Coleta e retenção dependem do ambiente |
| Qualidade | `.github/workflows/tests.yml` e `quality.yml` | Pytest, RAG em Qdrant, smoke Neo4j, frontend, imagem, migrations e gates. Promptfoo requer infraestrutura e disparo próprio | Definições de workflow inspecionadas | Automação declarada. Resultado de execução desta baseline não inferido |

No drift, `unverified` preserva o baseline quando a aquisição ou a
leitura falha. A primeira observação cria o baseline. Mudança aditiva
atualiza o histórico sem incidente. Mudança breaking tenta o caminho de
incidente e preserva o baseline anterior se a entrega falhar.
`--no-emit` muda deliberadamente esse comportamento. A execução do CLI
é uma observação pontual.

### Resultados históricos e fronteiras de avaliação

O [benchmark DA-29](RERANKER_BENCHMARK.md) registra a reexecução de
18/10/2026 em CPU, sem GPU, com reranqueamento do corpus inteiro.
No dataset ampliado de 90 consultas no escopo e 15 documentos, o
`mmarco-mMiniLMv2` obteve Hit@1 de 88/90, frente a 87/90 do L6.
O ganho pareado não foi significativo no teste registrado. Esse desenho
mede ranking de documentos. Acerto do diagnóstico, resolução de
incidentes e calibração exigem protocolos de avaliação próprios.

A inspeção do mapa `GATES` em `app/evaluation/gates.py` identificou
23 entradas. Os workflows declaram verificações distintas para
segurança, Compose, Qdrant, Neo4j, frontend, imagem e PostgreSQL.
O gate do modelo de linguagem usa promptfoo com Ollama e Qdrant e
baseline versionada. Resultados históricos de outro modelo permanecem
vinculados àquele modelo e à configuração que os produziu.

### Verificação complementar de rota

Foram feitas tentativas HTTP em processo para a regra de IDoc com
falha de consulta simulada. Tanto `TestClient` quanto `ASGITransport`
ficaram sem resposta dentro dos limites do ensaio. O primeiro
travou na espera do portal AnyIO; o segundo também não devolveu
resultado. Isso não estabelece o status HTTP com Qdrant desligado.
A execução focada com conftest para autenticação, administração,
fila, drift e redação também não produziu resultado no tempo limite.

Os seis corpos JSON do manual passaram em `model_validate`, usando
`IncidentRequest` ou `IncidentEventEnvelope` na cópia Python 3.12.
Esse teste verifica forma e valores do schema. Os exemplos continuam
ilustrativos para comportamento do conector, modelo e worker.

O [registro k3d de 18/10/2026](EVIDENCIA_K3D_2026-10-18.md) descreve
um cluster de um nó com Ollama no host, rollout de API e worker,
indexação, readiness e cenários HTTP. O HPA passou de uma para duas
réplicas naquele ensaio. Esse é o alcance da evidência histórica local.
APIRule e operação em Kyma gerenciado permanecem verificação separada.

O artefato `data/eval/promptfoo_baseline.json` registra
`qwen3.6:35b-a3b`, 17 casos e taxa de aprovação 1,0 em 13/10/2026.
A suíte inclui caminhos determinísticos. O placar do artefato
não equivale a 17 inferências do LLM nem à aprovação de outro
modelo. A inspeção também encontrou `gpt-4o-mini` na configuração
Kyma, o que explica o aviso de divergência do gate.

A versão final do quality gate estático foi executada na cópia
rastreada com `python scripts/quality_gate.py --json /tmp/iic-quality-final.json`.
Resultado de 05/11/2026: 21 PASS, 2 WARN, zero FAIL. Os avisos se
referem à vantagem pontual do BGE no ranking e ao modelo Kyma distinto
do baseline. O primeiro ensaio falhou em duas referências a estado de
ingestão gerado localmente. A documentação passou a distinguir nome
de arquivo gerado e diretório, e o gate foi reexecutado.

### Reexecução local fora do sandbox

Na cópia de `git archive`, com Python 3.12, sem arquivo de ambiente
do checkout e com `HF_HUB_OFFLINE=1`, 138 testes passaram em 7,79 s.
O recorte executou `test_auth.py`, `test_admin_security.py`,
`test_admin_ui_csp.py`, `test_queue.py`, `test_contracts_diff.py`,
`test_contracts_observe.py` e `test_dlp_ner.py`, via
`python -m pytest -p no:cacheprovider -q`.

O ensaio de rota usou HTTPX `ASGITransport` sem lifespan, chave de teste
e Qdrant real como cliente HTTP configurado para uma porta loopback
sem listener. Vetores densos e esparsos foram sintéticos. A consulta
ao Qdrant registrou indisponibilidade. O cenário RFC demo de IDoc 51
retornou `/health` 200, `/ready` 503 e `/diagnose` 200, com
`matched_source=rule_engine:sap_idoc_status_51`, versão e digest de prompt
nulos e zero chamadas de LLM no orçamento. O resultado cobre esse
caminho determinístico e a falha de consulta. Inicialização real do
modelo de embedding, startup de servidor e fluxo de inferência
continuam fora do ensaio.

A execução carregou 28 regras combinadas em
`app/agent/ontology_loader.py::load_error_rules_combined`. As 22
declarações em `KNOWN_ERROR_RULES` são o catálogo Python de fallback.
Essa carga SKOS de regras é independente de ligar o nó
`ontology_debug` após o relatório.

A suíte sem integração foi reexecutada na mesma cópia fora do sandbox
com `python -m pytest tests -m 'not integration' -p no:cacheprovider -q`.
O ambiente usou Python 3.12, `HF_HUB_OFFLINE=1` e Qdrant em porta
loopback sem serviço. Resultado em 05/11/2026: 1.892 aprovados,
2 falhas, 24 pulados, 17 desmarcados e 2 avisos em 49,33 s.
As falhas em `test_da65_rag_degradation.py` (perna híbrida) e
`test_ingest.py` (contagem de erro na ingestão) ocorreram ao carregar
`Qdrant/bm25` ausente no cache em modo offline. O resultado preserva
essa limitação da suíte e do ambiente.

O Mermaid CLI também gerou os 28 PNGs. A revisão visual amostral
incluiu contexto, contêineres, fluxo gerado, deployment Kyma,
contratos e sequências. A inspeção por amostragem é distinta da
validação sintática de todos os blocos.

### Verificação de navegação, diagramas e serviços em 06/11/2026

- Foram conferidas 219 referências a âncoras Markdown locais. Índices
  de decisões em inglês apontam para a decisão em português quando a
  tradução da seção está ausente. Nenhuma âncora ficou sem destino na
  verificação final.
- Foram verificadas 58 URLs distintas de documentação e referências.
  Dois destinos públicos retornaram 404 e foram substituídos por páginas
  oficiais que responderam 200. O endereço de sessão privada retornou
  403 sem credenciais. Endpoints de exemplo e endereços de infraestrutura
  ficam fora dessa contagem.
- O Mermaid CLI renderizou os 106 blocos finais para SVG. Foram
  corrigidos rótulos inválidos no tutorial k3d e no diagrama da UI. A
  arquitetura em inglês aponta para o grafo gerado principal, evitando
  um segundo desenho divergente.
- Em cópia limpa do commit, FastAPI com lifespan, Qdrant 1.19.0 e
  Redis 7.4 reais, o cenário determinístico IDoc 51 passou via REST,
  fila e worker RQ, webhook CloudEvents e A2A. O teste também obteve
  liveness e readiness 200. O Qdrant tinha coleção de teste vazia,
  embeddings sintéticos e o conector estava em demo. O worker RQ
  rodou em modo burst no processo de teste.
- Com Neo4j Community 5.26.0 real, `pytest --noconftest -q
  tests/test_graph_store_neo4j_smoke.py` passou com quatro testes.
- Em PostgreSQL 16 descartável, `alembic upgrade head` aplicou as
  migrations. `alembic check` detectou divergência do índice
  `uq_prompt_versions_current`, criado pela migration de versões de
  prompt e ausente no metadata comparado. A cadeia interrompeu antes
  de validar as consultas Grafana. O achado exige correção no ORM ou
  no schema, fora da atualização documental.
- A rodada não executou inferência real, embeddings reais, conexão
  AMQP, chamadas aos conectores de fornecedores ou implantação Kyma.
