# Documentação — Integration Incident Copilot

Índice de `docs/`. O [`README.md` da raiz](../README.md) apresenta o projeto;
a prosa de cada decisão de arquitetura está em
[`DECISOES_DE_ARQUITETURA.md`](DECISOES_DE_ARQUITETURA.md); este diretório
guarda também guias de uso, referência operacional e registros de processo.

**Regra de ouro:** quando um documento diverge do código, o código vence.
Se encontrar uma referência quebrada, corrija o documento; se o código
estiver errado, corrija o código e, se for uma decisão, registre uma DA. Ver
[`QUALITY_GATES.md`](QUALITY_GATES.md) para o que é verificado
automaticamente a cada build.

---

## Comece por aqui

| Você quer… | Leia, nesta ordem |
|---|---|
| Rodar pela primeira vez | [`GETTING_STARTED.md`](GETTING_STARTED.md) → [`USER_GUIDE.md`](USER_GUIDE.md) |
| Levar para um ambiente novo | [`DEPLOY.md`](DEPLOY.md) → [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) |
| Entender o que existe hoje | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Entender *por que* foi feito assim | [`DECISOES_DE_ARQUITETURA.md`](DECISOES_DE_ARQUITETURA.md) (índice das 50 DAs registradas, numeradas de DA-1 a DA-60) |
| Debugar um incidente real | [`TUTORIAL_ARQUITETURA_DEBUG.md`](TUTORIAL_ARQUITETURA_DEBUG.md) |
| Mudar a base de conhecimento (RAG) | `python -m app.rag.ingest --help` (referência no docstring de `app/rag/ingest.py`) |
| Entender a qualidade do LLM | [`RERANKER_BENCHMARK.md`](RERANKER_BENCHMARK.md) |

---

## Referência atual

Descrevem o sistema como ele é **agora**. Se divergirem do código, é bug
deste documento.

| Documento | Assunto |
|---|---|
| [`GETTING_STARTED.md`](GETTING_STARTED.md) | Do zero ao primeiro diagnóstico em menos de 10 minutos |
| [`USER_GUIDE.md`](USER_GUIDE.md) | Guia de operação: autenticação, todas as superfícies (REST, UI, CLI, MCP, A2A, eventos), leitura do resultado, loop de verificação e onde ficam os logs por camada |
| [`DEPLOY.md`](DEPLOY.md) | Caminho Docker Compose |
| [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) | Diagnóstico de falha por sintoma (startup, AMQP, RAG, LLM, Kyma) |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Visão técnica do que existe no código, com os diagramas (C4, grafo gerado, estado, segurança, implantação). Local alternativo declarado para a prosa das DA-32/33/34/35 |
| [`DECISOES_DE_ARQUITETURA.md`](DECISOES_DE_ARQUITETURA.md) | Índice e prosa das decisões de arquitetura (DA-1 a DA-60): problema, solução, validação e limitações |
| [`CONNECTORS.md`](CONNECTORS.md) | Os 10 conectores: variáveis, cenários demo, validação contra sistema real, como adicionar um novo |
| [`QUALITY_GATES.md`](QUALITY_GATES.md) | O que os gates de qualidade verificam a cada build — **e o que eles não verificam** |
| [`RERANKER_BENCHMARK.md`](RERANKER_BENCHMARK.md) | Benchmark que fixou o reranker canônico (DA-29) |
| [`COVERAGE_MAP.md`](COVERAGE_MAP.md) | Mapa produto SAP × mecanismo: o que tem conector e o que é só cliente genérico (**gerado** — não editar à mão) |

---

## Casos de uso

| Documento | Assunto |
|---|---|
| [`CASOS_DE_USO.md`](CASOS_DE_USO.md) | Os nove cenários de uso, funcional e técnico no mesmo documento; cada número vem de `tests/test_casos_de_uso.py` |

## Processo e contexto

Descrevem **como** o projeto foi construído. Registros de decisão com
números e nomes próprios do momento em que foram escritos; a verdade
atual está no código, no `ARCHITECTURE.md` e nos testes.

| Documento | Assunto |
|---|---|
| [`TUTORIAL_ARQUITETURA_DEBUG.md`](TUTORIAL_ARQUITETURA_DEBUG.md) | Da requisição ao relatório, com breakpoints no VS Code |
| [`TUTORIAL_ACESSIBILIDADE_MULTIVENDOR.md`](TUTORIAL_ACESSIBILIDADE_MULTIVENDOR.md) | LLM Gateway e conector multi-vendor, passo a passo (Fase 8) |
| [`TUTORIAL_FASE9_MULTIVENDOR_GRAPHRAG_A2A.md`](TUTORIAL_FASE9_MULTIVENDOR_GRAPHRAG_A2A.md) | Complementar ao anterior: GraphRAG e A2A (Fase 9) |
| [`TCO_SAP_AI_CORE_VS_SELF_HOSTED.md`](TCO_SAP_AI_CORE_VS_SELF_HOSTED.md) | SAP AI Core vs. IA local sob medida — comparação de custo para conversa com cliente |

---

## Notas de manutenção

Documentos em `docs/` **não** são todos updated juntos. Ao acrescentar um
documento novo, ligue-o neste índice — um `.md` órfão nesta pasta é
indiscoverível, e foi exatamente o que aconteceu com 9 destes 17 arquivos
até 2026-09-29, quando este índice foi criado.

Os gates de qualidade (DA-51) verificam o índice e a prosa das DAs em
`DECISOES_DE_ARQUITETURA.md`, links, referências `arquivo.py::símbolo` e
variáveis de ambiente citadas em toda esta pasta, e `tests/test_diagramas.py`
amarra os diagramas ao código. **Eles não verificam** a prosa em si: o resto
é revisão manual.
