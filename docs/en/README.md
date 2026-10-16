# Documentation — Integration Incident Copilot

> English translation of [`docs/README.md`](../README.md) · source commit `812800c` · [Português](../README.md)
> If this page and the Portuguese source differ, the code and the Portuguese source prevail.

Index of `docs/`. The [`README.md` at the root](../README.md) introduces the project;
the prose of each architecture decision is in
[`DECISOES_DE_ARQUITETURA.md`](../DECISOES_DE_ARQUITETURA.md); this directory
also holds usage guides, operational reference, and process records (Portuguese only).

**Golden rule:** when a document diverges from the code, the code wins.
If you find a broken link, fix the document; if the code
is wrong, fix the code and, if it's a decision, register a DA. See
[`QUALITY_GATES.md`](../QUALITY_GATES.md) for what is automatically verified
at each build (Portuguese only).

---

## Start here

| You want to… | Read, in this order |
|---|---|
| **Learn the whole project, following the application in the debugger** | [`MANUAL.md`](../MANUAL.md) — single didactic source, 17 chapters, each with a debug session (Portuguese only) |
| Run for the first time | [`GETTING_STARTED.md`](GETTING_STARTED.md) → [`USER_GUIDE.md`](USER_GUIDE.md) |
| Take to a new environment | [`DEPLOY.md`](../DEPLOY.md) → [`TROUBLESHOOTING.md`](../TROUBLESHOOTING.md) (Portuguese only) |
| Understand what exists today | [`ARCHITECTURE.md`](../ARCHITECTURE.md) (Portuguese only) |
| Understand *why* it was done this way | [`DECISOES_DE_ARQUITETURA.md`](../DECISOES_DE_ARQUITETURA.md) (Portuguese only; index of 50 registered DAs, numbered DA-1 to DA-60) |
| Debug a real incident | [`TUTORIAL_ARQUITETURA_DEBUG.md`](../TUTORIAL_ARQUITETURA_DEBUG.md) (Portuguese only) |
| Change the knowledge base (RAG) | `python -m app.rag.ingest --help` (reference in the docstring of `app/rag/ingest.py`) |
| Understand LLM quality | [`RERANKER_BENCHMARK.md`](../RERANKER_BENCHMARK.md) (Portuguese only) |

---

## Current reference

Describe the system as it is **now**. If they diverge from the code, it's a bug
of this document.

| Document | Subject |
|---|---|
| [`MANUAL.md`](../MANUAL.md) | **Single didactic manual**: from environment to Kyma, in the order data flows through the system; each chapter closes with a breakpoint, command and expected output. Appendices: the 14 `launch.json` configurations, symptom → breakpoint map, and project numbers with the command that reconfirms them (Portuguese only) |
| [`GETTING_STARTED.md`](GETTING_STARTED.md) | From zero to first diagnosis in less than 10 minutes |
| [`USER_GUIDE.md`](USER_GUIDE.md) | Operational guide: authentication, all surfaces (REST, UI, CLI, MCP, A2A, events), reading the result, verification loop and where logs are by layer |
| [`DEPLOY.md`](../DEPLOY.md) | Docker Compose path (Portuguese only) |
| [`TROUBLESHOOTING.md`](../TROUBLESHOOTING.md) | Failure diagnosis by symptom (startup, AMQP, RAG, LLM, Kyma) (Portuguese only) |
| [`ARCHITECTURE.md`](../ARCHITECTURE.md) | Technical view of what exists in the code, with the diagrams (C4, generated graph, state, security, deployment). Alternate declared location for the prose of DA-32/33/34/35 (Portuguese only) |
| [`DECISOES_DE_ARQUITETURA.md`](../DECISOES_DE_ARQUITETURA.md) | Index and prose of architecture decisions (DA-1 to DA-60): problem, solution, validation and limitations (Portuguese only) |
| [`CONNECTORS.md`](CONNECTORS.md) | The 10 connectors: variables, demo scenarios, validation against real system, how to add a new one |
| [`QUALITY_GATES.md`](../QUALITY_GATES.md) | What quality gates verify at each build — **and what they do not verify** (Portuguese only) |
| [`RERANKER_BENCHMARK.md`](../RERANKER_BENCHMARK.md) | Benchmark that fixed the canonical reranker (DA-29) (Portuguese only) |
| [`COVERAGE_MAP.md`](../COVERAGE_MAP.md) | SAP product × mechanism map: what has connector and what is only generic client (**generated** — do not edit manually) (Portuguese only) |

---

## Use cases

| Document | Subject |
|---|---|
| [`CASOS_DE_USO.md`](../CASOS_DE_USO.md) | The nine use cases, functional and technical in the same document; each number comes from `tests/test_casos_de_uso.py` (Portuguese only) |

---

## Process and context

Describe **how** the project was built. Decision records with
numbers and proper names from the moment they were written; the current
truth is in the code, in `ARCHITECTURE.md` and in the tests.

| Document | Subject |
|---|---|
| [`TUTORIAL_ARQUITETURA_DEBUG.md`](../TUTORIAL_ARQUITETURA_DEBUG.md) | From request to report, with breakpoints in VS Code (Portuguese only) |
| [`TUTORIAL_ACESSIBILIDADE_MULTIVENDOR.md`](../TUTORIAL_ACESSIBILIDADE_MULTIVENDOR.md) | LLM Gateway and multi-vendor connector, step by step (Phase 8) (Portuguese only) |
| [`TUTORIAL_FASE9_MULTIVENDOR_GRAPHRAG_A2A.md`](../TUTORIAL_FASE9_MULTIVENDOR_GRAPHRAG_A2A.md) | Complement to the previous: GraphRAG and A2A (Phase 9) (Portuguese only) |
| [`TCO_SAP_AI_CORE_VS_SELF_HOSTED.md`](../TCO_SAP_AI_CORE_VS_SELF_HOSTED.md) | SAP AI Core vs. local AI custom — cost comparison for customer conversation (Portuguese only) |
| [`AVALIACAO_MODELOS_LOCAIS.md`](../AVALIACAO_MODELOS_LOCAIS.md) | Empirical evaluation of local models (refactoring harness) and environment adjustment for OpenCode — method, results and provenance (2026-10-11) (Portuguese only) |

---

## Maintenance notes

Documents in `docs/` are **not** all updated together. When adding a
new document, link it in this index — a `.md` orphan in this folder is
undiscoverable, and it was exactly what happened to 9 of these 17 files
until 2026-09-29, when this index was created.

The quality gates (DA-51) verify the index and the prose of the DAs in
`DECISOES_DE_ARQUITETURA.md`, links, references `file.py::symbol` and
environment variables cited throughout this folder, and `tests/test_diagramas.py`
ties the diagrams to the code. **They do not verify** the prose itself: the rest
is manual review.

---

## Status of this translation

| Document | Português | English | Status |
|---|---|---|---|
| `README.md` | `docs/README.md` | `docs/en/README.md` | ✅ |
| `GETTING_STARTED.md` | `docs/GETTING_STARTED.md` | `docs/en/GETTING_STARTED.md` | ✅ |
| `USER_GUIDE.md` | `docs/USER_GUIDE.md` | `docs/en/USER_GUIDE.md` | ✅ |
| `CONNECTORS.md` | `docs/CONNECTORS.md` | `docs/en/CONNECTORS.md` | ✅ |
| `ARCHITECTURE.md` | `docs/ARCHITECTURE.md` | — | 🇵🇹 only |
| `QUALITY_GATES.md` | `docs/QUALITY_GATES.md` | — | 🇵🇹 only |
| `DEPLOY.md` | `docs/DEPLOY.md` | — | 🇵🇹 only |
| `TROUBLESHOOTING.md` | `docs/TROUBLESHOOTING.md` | — | 🇵🇹 only |
| `CASOS_DE_USO.md` | `docs/CASOS_DE_USO.md` | — | 🇵🇹 only |
| `DECISOES_DE_ARQUITETURA.md` | `docs/DECISOES_DE_ARQUITETURA.md` | — | 🇵🇹 only |
| `MANUAL.md` | `docs/MANUAL.md` | — | 🇵🇹 only |
| `TUTORIAL_ARQUITETURA_DEBUG.md` | `docs/TUTORIAL_ARQUITETURA_DEBUG.md` | — | 🇵🇹 only |
| `TUTORIAL_ACESSIBILIDADE_MULTIVENDOR.md` | `docs/TUTORIAL_ACESSIBILIDADE_MULTIVENDOR.md` | — | 🇵🇹 only |
| `TUTORIAL_FASE9_MULTIVENDOR_GRAPHRAG_A2A.md` | `docs/TUTORIAL_FASE9_MULTIVENDOR_GRAPHRAG_A2A.md` | — | 🇵🇹 only |
| `TCO_SAP_AI_CORE_VS_SELF_HOSTED.md` | `docs/TCO_SAP_AI_CORE_VS_SELF_HOSTED.md` | — | 🇵🇹 only |
| `AVALIACAO_MODELOS_LOCAIS.md` | `docs/AVALIACAO_MODELOS_LOCAIS.md` | — | 🇵🇹 only |
| `RERANKER_BENCHMARK.md` | `docs/RERANKER_BENCHMARK.md` | — | 🇵🇹 only |
| `COVERAGE_MAP.md` | `docs/COVERAGE_MAP.md` | — | 🇵🇹 only (generated) |

Legend:
- ✅ Translated
- 🔄 In progress
- ⏳ Planned
- 🇵🇹 Portuguese only (study/reference material)
