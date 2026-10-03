# Documentação — Integration Incident Copilot

Índice de `docs/`. A prosa de decisão canônica do projeto vive no
[`README.md` da raiz](../README.md); este diretório guarda guias de uso,
referência operacional e registros de processo.

**Regra de ouro:** quando um documento disagree do código, o código vence.
Se encontrar uma referência quebrada, corrija o documento — ou, se o
código estiver errado, abra a DA-53 do caminho. Ver
[`QUALITY_GATES.md`](QUALITY_GATES.md) para o que é verificado
automaticamente a cada build.

---

## Comece por aqui

| Você quer… | Leia, nesta ordem |
|---|---|
| Rodar pela primeira vez | [`GETTING_STARTED.md`](GETTING_STARTED.md) → [`USER_GUIDE.md`](USER_GUIDE.md) |
| Levar para um ambiente novo | [`DEPLOYMENT.md`](DEPLOYMENT.md) (todos os cenários, passo a passo) → [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) |
| Entender o que existe hoje | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Entender *por que* foi feito assim | [`README.md` da raiz](../README.md) (índice de DAs) |
| Debugar um incidente real | [`TUTORIAL_ARQUITETURA_DEBUG.md`](TUTORIAL_ARQUITETURA_DEBUG.md) |
| Mudar a base de conhecimento (RAG) | [`INGEST_REFERENCE.md`](INGEST_REFERENCE.md) |
| Entender a qualidade do LLM | [`RERANKER_BENCHMARK.md`](RERANKER_BENCHMARK.md) |

---

## Referência atual

Descrevem o sistema como ele é **agora**. Se divergirem do código, é bug
deste documento.

| Documento | Assunto |
|---|---|
| [`GETTING_STARTED.md`](GETTING_STARTED.md) | Do zero ao primeiro diagnóstico em menos de 10 minutos |
| [`USER_GUIDE.md`](USER_GUIDE.md) | Guia de operação: autenticação, todas as superfícies (REST, UI, CLI, MCP, A2A, eventos), leitura do resultado, loop de verificação e onde ficam os logs por camada |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | **Documento único de implantação**: 9 cenários (avaliação local, dev, container, produção com auth, Kyma, cloud gerenciado + soberania, on-premise com sizing do Ollama), cada um com passo a passo próprio e status explícito |
| [`DEPLOY.md`](DEPLOY.md) | Detalhe do caminho Docker Compose (complemento do `DEPLOYMENT.md`) |
| [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) | Diagnóstico de falha por sintoma (startup, AMQP, RAG, LLM, Kyma) |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Visão técnica do que existe no código. Local alternativo declarado para a prosa das DA-32/33/34/35 |
| [`INGEST_REFERENCE.md`](INGEST_REFERENCE.md) | Referência do `app.rag.ingest` — indexação da base de conhecimento |
| [`QUALITY_GATES.md`](QUALITY_GATES.md) | O que os gates de qualidade verificam a cada build — **e o que eles não verificam** |
| [`RERANKER_BENCHMARK.md`](RERANKER_BENCHMARK.md) | Benchmark que fixou o reranker canônico (DA-29) |
| [`COVERAGE_MAP.md`](COVERAGE_MAP.md) | Mapa produto SAP × mecanismo: o que tem conector e o que é só cliente genérico (**gerado** — não editar à mão) |

---

## Auditoria e use cases

Use cases reais mapeados ponta a ponta (HTTP → response), com foco em
rastreabilidade, encadeamento de módulos e identificação de lacunas.

| Documento | Assunto |
|---|---|
| [`AUDITORIA_PONTA_A_PONTA.md`](AUDITORIA_PONTA_A_PONTA.md) | Use cases primários (_UC-1_ a _UC-9_): mapa completo de processamento, breakpoints estrategicos (DA-23), mapping de módulos |
| [`UC_01_SAP_IDOC_STUCK.md`](UC_01_SAP_IDOC_STUCK.md) | Use Case 1: Incidente SAP OData (IDoc stuck) — fluxo completo BP-1 a BP-8 |
| [`UC_02_SERVICENOW.md`](UC_02_SERVICENOW.md) | Use Case 2: ServiceNow (multi-fornecedor SaaS) — supervisor roteamento, checklist DA-57 |
| [`UC_03_GENERIC_WEB_SEARCH.md`](UC_03_GENERIC_WEB_SEARCH.md) | Use Case 3: Generic + web search fallback — DA-57 (fail-closed, sem fallback fixo) |
| [`UC_05_WEAK_EVIDENCE_FALLBACK.md`](UC_05_WEAK_EVIDENCE_FALLBACK.md) | Use Case 5: Evidence fraca → reference library fallback (DA-15/17) — capping de confiança |
| [`UC_06_CLOUD_FALLBACK.md`](UC_06_CLOUD_FALLBACK.md) | Use Case 6: Cloud fallback (Ollama offline) — hybrid inference (DA-20/26/43/48) |
| [`UC_07_CLOUDEVENTS_WEBHOOK.md`](UC_07_CLOUDEVENTS_WEBHOOK.md) | Use Case 7: CloudEvents webhook (DA-23/32/40) — event mesh, AMQP 1.0, idempotência |
| [`UC_08_GRAPHRAG_ENABLED.md`](UC_08_GRAPHRAG_ENABLED.md) | Use Case 8: GraphRAG enabled (Neo4j) — cypher queries, upsert graph (DA-21/28) |
| [`UC_09_CONTRACT_DRIFT_BREAKING.md`](UC_09_CONTRACT_DRIFT_BREAKING.md) | Use Case 9: Contract drift breaking (DA-52) — EDMX parse, fingerprint, baseline, observed |

## Processo e contexto

Descrevem **como** o projeto foi construído. Registros de decisão com
números e nomes próprios do momento em que foram escritos; a verdade
atual está no código e no `README.md` da raiz.

| Documento | Assunto |
|---|---|
| [`PROCESSO_DESENVOLVIMENTO.md`](PROCESSO_DESENVOLVIMENTO.md) | O processo real seguido na construção do projeto |
| [`GUIA_DE_ESTUDOS.md`](GUIA_DE_ESTUDOS.md) | Síntese de aprendizado, para releitura e consolidação |
| [`TUTORIAL_ARQUITETURA_DEBUG.md`](TUTORIAL_ARQUITETURA_DEBUG.md) | Da requisição ao relatório, com breakpoints no VS Code |
| [`TUTORIAL_ACESSIBILIDADE_MULTIVENDOR.md`](TUTORIAL_ACESSIBILIDADE_MULTIVENDOR.md) | LLM Gateway e conector multi-vendor, passo a passo (Fase 8) |
| [`TUTORIAL_FASE9_MULTIVENDOR_GRAPHRAG_A2A.md`](TUTORIAL_FASE9_MULTIVENDOR_GRAPHRAG_A2A.md) | Complementar ao anterior: GraphRAG e A2A (Fase 9) |
| [`TCO_SAP_AI_CORE_VS_SELF_HOSTED.md`](TCO_SAP_AI_CORE_VS_SELF_HOSTED.md) | SAP AI Core vs. IA local sob medida — comparação de custo para conversa com cliente |
| [`ferramentas-sustentacao-ecossistema.md`](ferramentas-sustentacao-ecossistema.md) | Ferramentas para sustentação e evolução do ecossistema |
| [`a2a-interoperability-layer.md`](a2a-interoperability-layer.md) | Proposta da camada A2A — **já implementada** em `app/a2a/`; mantida como registro do desenho |

---

## Notas de manutenção

Documentos em `docs/` **não** são todos updated juntos. Ao acrescentar um
documento novo, ligue-o neste índice — um `.md` órfão nesta pasta é
indiscoverível, e foi exatamente o que aconteceu com 9 destes 17 arquivos
até 2026-09-29, quando este índice foi criado.

Os gates de qualidade (DA-51) verificam a prosa de DA no `README.md` da
raiz e as invariantes de avaliação; **eles não verificam** o conteúdo
desta pasta. Correções aqui são revisão manual.
